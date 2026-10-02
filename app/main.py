"""Run `python -m app.main check` from the project root."""

import argparse
import json
import logging
import sqlite3
import sys
import webbrowser
from datetime import UTC, datetime, timedelta
from logging.handlers import RotatingFileHandler

from app.analyst import AnalysisError, Analyst, CodexAnalyst, GeminiAnalyst, OpenAIAnalyst, TemplateAnalyst
from app.config import ROOT, Settings, load_watchlist
from app.database import AlreadyRunning, Database, run_lock
from app.fetcher import DataError, MockStockProvider, StockProvider, TwelveDataProvider
from app.indicators import calculate
from app.notifier import ConsoleNotifier, LineNotifier, NotificationError, Notifier
from app.rules import analysis_payload, evaluate

log = logging.getLogger("stock_manager")


def setup_logging(level: str) -> None:
    (ROOT / "logs").mkdir(exist_ok=True)
    formatter = logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", "%Y-%m-%d %H:%M:%S")
    log.setLevel(level)
    log.handlers.clear()
    for handler in [logging.StreamHandler(), RotatingFileHandler(ROOT / "logs/stock-manager.log",
            maxBytes=1_000_000, backupCount=3, encoding="utf-8")]:
        handler.setFormatter(formatter)
        log.addHandler(handler)
    # Prevent HTTP debug logs from exposing headers, query parameters or API keys.
    for name in ("httpx", "httpcore", "urllib3", "openai"):
        logging.getLogger(name).setLevel(logging.CRITICAL)


def deliver_pending(db: Database, analyst: Analyst, notifier: Notifier, now: datetime,
                    exclude: set[str] | None = None, settings: Settings | None = None) -> tuple[int, int]:
    settings = settings or Settings()
    sent = errors = 0
    for row in db.pending():
        if exclude and row["id"] in exclude:
            continue
        # Never redirect a previously queued message after a configuration change.
        if row["channel"] != notifier.channel or row["recipient"] != notifier.recipient:
            log.error("Pending %s has a different delivery destination; review history", row["id"])
            errors += 1
            continue
        if now - datetime.fromisoformat(row["created_at"]) >= timedelta(hours=23):
            db.finish(row["id"], "expired", now, "Older than 23 hours; inspect manually")
            log.warning("Notification %s expired; see show-alerts", row["id"])
            errors += 1
            continue
        message = row["message"]
        if message is None:
            payload = json.loads(row["payload"])
            try:
                from app.budget_planner import ai_stock_limit
                if analyst.uses_ai and not db.reserve_ai(row["id"], payload["symbol"], now,
                        settings.ai_max_calls_per_day, ai_stock_limit(settings, payload['symbol'], now)):
                    message = TemplateAnalyst().summarize(payload) + "\nหมายเหตุ: ใช้แม่แบบเพื่อจำกัดการเรียก AI หรือเพราะเหตุการณ์นี้เคยจองการเรียกแล้ว"
                else:
                    message = analyst.summarize(payload)
            except AnalysisError:
                log.warning("AI unavailable; sending a labelled local explanation")
                message = TemplateAnalyst().summarize(payload) + "\nหมายเหตุ: AI ขัดข้อง จึงใช้แม่แบบสำรอง"
            from app.research_context import source_buttons
            references=source_buttons(payload)
            if references:
                message+='\n\nแหล่งข้อมูลประกอบ\n'+references
            if payload.get('caught_up_close'):
                from app.voice import thai_time
                message = ('ตามเก็บสัญญาณจากราคาปิดสหรัฐ '+payload['caught_up_close']+' ครับ\n'
                           'ราคาปิด ณ '+thai_time(payload['quote_as_of'])+' (เวลาไทย)\n'
                           'ตรวจพบย้อนหลังเมื่อ '+thai_time(now.isoformat())+' (เวลาไทย)\n'
                           'เป็นข้อมูลราคาปิดย้อนหลัง อ่านวันที่ราคาก่อนพิจารณาครับ\n\n'+message)
            db.set_message(row["id"], message)
        db.start_attempt(row["id"], now)
        try:
            notifier.send(message, row["id"], row["recipient"])
        except NotificationError as exc:
            status = "pending" if exc.retryable else "failed"
            db.finish(row["id"], status, now, str(exc))
            log.error("Notification %s: %s (status=%s)", row["id"], exc, status)
            errors += 1
        else:
            db.finish(row["id"], "accepted", now)
            log.info("Notification accepted by %s | id=%s", notifier.channel, row["id"])
            sent += 1
    return sent, errors


def check(settings: Settings, provider: StockProvider, analyst: Analyst, notifier: Notifier,
          now: datetime | None = None, scheduled: bool = False) -> dict:
    now = now or datetime.now(UTC)
    stocks = settings.stocks()
    checked = queued = sent = errors = 0
    with run_lock(settings.database_path.with_suffix(".lock")):
        if scheduled:
            from app.market import is_open
            from app.market_state import MarketState
            if settings.mock_mode or not is_open(now):
                log.info("Scheduled check skipped: mock mode or market closed/unknown calendar")
                return dict(checked=0, queued=0, sent=0, errors=0)
            if not MarketState(settings.database_path.parent / "market-api.sqlite3").claim_slot(now):
                log.info("Scheduled check skipped: this five-minute slot already ran")
                return dict(checked=0, queued=0, sent=0, errors=0)
            from app.budget_planner import due_symbols
            due = due_symbols(settings, now)
            stocks = [s for s in stocks if s.symbol in due]
            from app.portfolio_catalog import PortfolioCatalog
            weights = PortfolioCatalog(settings).weights()[1]
            stocks.sort(key=lambda s:(-weights.get(s.symbol,0),s.symbol))
        db = Database(settings.database_path, "mock" if settings.mock_mode else "live")
        run_id = db.start_run(now)
        try:
            # Retry existing outbox once per run, then fetch new observations.
            sent, errors = deliver_pending(db, analyst, notifier, now, settings=settings)
            old_pending = {row["id"] for row in db.pending()}
            for stock in stocks:
                try:
                    snapshot = provider.fetch(stock.symbol, now)
                    if snapshot.simulated != settings.mock_mode or snapshot.symbol != stock.symbol:
                        raise DataError("Provider mode or symbol mismatch")
                    snapshot.validate(now, settings.max_quote_age_hours)
                    db.save_snapshot(snapshot)
                    indicators = calculate([bar.close for bar in snapshot.bars], snapshot.price,
                                           snapshot.previous_close, stock.target_price)
                    if len(snapshot.bars) < 51:
                        log.warning("%s | only %d daily bars; unavailable indicators skipped", stock.symbol, len(snapshot.bars))
                    from app.alert_policy import effective_settings, policies
                    rule_settings = effective_settings(settings, stock.symbol)
                    events = evaluate(snapshot, indicators, stock, rule_settings)
                    events += db.price_recovery(snapshot, indicators, rule_settings)
                    eligible = db.eligible(events, now, settings.cooldown_hours)
                    if eligible:
                        payload = analysis_payload(snapshot, indicators, eligible)
                        payload['alert_policies'] = policies(settings, stock.symbol)
                        if snapshot.price_kind == 'daily_close':
                            payload['caught_up_close'] = snapshot.session_date.isoformat()
                        if not settings.mock_mode:
                            from app.portfolio import load_portfolio, analyst_context
                            profile = load_portfolio(settings.database_path.parent, settings.portfolio_id)
                            if profile:
                                payload["portfolio_context"] = analyst_context(profile, stock.symbol,
                                    dict(price=snapshot.price, as_of=snapshot.as_of.isoformat(),
                                         previous_close=snapshot.previous_close, stale=False, price_kind=snapshot.price_kind))
                            from app.portfolio_catalog import PortfolioCatalog
                            catalog = PortfolioCatalog(settings)
                            affected = []
                            for entry in catalog.read()['portfolios']:
                                p = load_portfolio(settings.database_path.parent, entry['id'])
                                if p and any(h['symbol'] == stock.symbol for h in p['holdings']):
                                    affected.append(dict(name=entry['name'], id=entry['id'],
                                        context=analyst_context(p, stock.symbol, dict(price=snapshot.price,
                                            as_of=snapshot.as_of.isoformat(), previous_close=snapshot.previous_close,
                                            stale=False, price_kind=snapshot.price_kind))))
                            payload['affected_portfolios'] = affected
                            watched = [e for e in catalog.read()['portfolios'] if stock.symbol in {s['symbol'] for s in e['stocks']}]
                            payload['tracked_in'] = [e['name'] for e in watched]
                            from app.investment_notes import compact_notes
                            payload['tracked_portfolio_notes'] = [dict(
                                name=e['name'], investment_notes=compact_notes(e.get('investment_notes')),
                                stock_notes=compact_notes(next((s.get('investment_notes') for s in e['stocks']
                                    if s['symbol'] == stock.symbol), None)))
                                for e in watched if e['id'] not in {p['id'] for p in affected}]
                            try:
                                from app.report import report_data
                                from app.research_context import context
                                from dataclasses import replace
                                chosen = affected[0]['id'] if affected else watched[0]['id'] if watched else settings.portfolio_id
                                evidence_settings = replace(settings,portfolio_id=chosen)
                                payload['decision_context'] = context(evidence_settings,report_data(evidence_settings,now),{stock.symbol},now)
                                payload['portfolio_context_available'] = bool(profile)
                            except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
                                log.warning('%s | saved research unavailable; using price evidence only',stock.symbol)
                        db.enqueue(eligible, payload, notifier.channel,
                                   notifier.recipient, now)
                        queued += 1
                    checked += 1
                    log.info("%s | price=%.2f USD | day=%+.2f%% | events=%d new=%d | quote=%s",
                             stock.symbol, snapshot.price, indicators.daily_change_pct, len(events), len(eligible),
                             snapshot.as_of.isoformat())
                except (DataError, ValueError, TypeError):
                    # Do not log raw upstream exceptions: they can contain credentials.
                    errors += 1
                    log.error("%s | fetch/data validation failed; skipped (check provider, quota or data completeness)", stock.symbol)
            # Avoid retrying earlier failures twice during the same run.
            new_sent, new_errors = deliver_pending(db, analyst, notifier, now, old_pending, settings)
            sent += new_sent
            errors += new_errors
            db.finish_run(run_id, datetime.now(UTC), checked, sent, errors)
        finally:
            db.close()
    result = dict(checked=checked, queued=queued, sent=sent, errors=errors)
    log.info("Check complete | checked=%d queued=%d accepted=%d errors=%d", checked, queued, sent, errors)
    return result


def sync_news(settings: Settings, now: datetime | None = None) -> dict:
    """Explicit, rate-limited official-release sync. Never called by price checks."""
    now = now or datetime.now(UTC)
    if settings.mock_mode or settings.news_mode != "press_releases" or not settings.stock_api_key:
        raise ValueError("News sync requires live mode, NEWS_MODE=press_releases and stock credentials")
    from app.news import NewsError, TwelveDataPressReleaseProvider
    with run_lock(settings.database_path.with_suffix(".lock")):
        db = Database(settings.database_path, "live")
        try:
            if not db.news_sync_due(now, settings.news_sync_min_hours):
                log.info("News sync skipped: last successful sync is within %.0f hours", settings.news_sync_min_hours)
                return {"saved": 0, "errors": 0, "skipped": True}
            provider = TwelveDataPressReleaseProvider(settings)
            saved = errors = 0
            for stock in settings.stocks():
                try:
                    saved += db.save_news(provider.fetch(stock.symbol, now), now)
                except NewsError:
                    errors += 1
                    log.warning("%s | official-news sync failed; no article was invented", stock.symbol)
            if not errors:
                db.mark_news_sync(now)
            return {"saved": saved, "errors": errors, "skipped": False}
        finally:
            db.close()


def run_portfolio_review(settings: Settings, now: datetime | None = None) -> dict:
    """One durable review per ISO week, from stored quotes/news. It does not fetch market data."""
    now = now or datetime.now(UTC)
    if settings.mock_mode:
        raise ValueError("Portfolio review requires live mode and a saved private portfolio")
    from app.report import report_data
    from app.review import create_review, local_review, period_key, review_payload
    overview = report_data(settings, now)
    profile = overview["portfolio"]
    if not profile:
        raise ValueError("Portfolio review requires data/portfolio-profile.json")
    from app.portfolio_catalog import PortfolioCatalog
    identity = PortfolioCatalog(settings).selected()['id']
    period = period_key(now)+((':portfolio:'+identity) if identity != 'main' else '')
    with run_lock(settings.database_path.with_suffix(".lock")):
        db = Database(settings.database_path, "live")
        try:
            previous = db.review(period)
            if previous:
                return {"created": False, "ai_used": bool(previous["ai_used"]), "period": period}
            payload = review_payload(profile, overview["news"])
            review_id = "review:" + period
            allowed = (settings.analyst_mode == "codex"
                       or (settings.analyst_mode == "gemini" and settings.gemini_share_portfolio_context))
            allowed = (allowed
                       and db.reserve_ai(review_id, "__portfolio_review__", now,
                                         settings.ai_max_calls_per_day, settings.ai_max_calls_per_stock_per_day))
            message, ai_used = create_review(settings, payload) if allowed else (local_review(payload), False)
            if settings.analyst_mode == "gemini" and not settings.gemini_share_portfolio_context:
                message += "\nหมายเหตุ: ใช้แม่แบบในเครื่องเพื่อไม่ส่งข้อมูลพอร์ตส่วนตัวไป Google"
            elif settings.analyst_mode in {"codex", "gemini"} and not allowed:
                message += "\nหมายเหตุ: ใช้แม่แบบเพื่อจำกัดการเรียก AI"
            db.save_review(period, now, message, len(overview["news"]), ai_used)
            return {"created": True, "ai_used": ai_used, "period": period}
        finally:
            db.close()


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Personal stock assistant (no trading)")
    parser.add_argument("command", choices=["check", "init-db", "show-alerts", "report", "view", "setup", "doctor", "test-codex", "refresh-history", "refresh-intraday", "news-sync", "review"])
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--no-open", action="store_true", help="Generate report without opening browser")
    parser.add_argument("--online", action="store_true", help="doctor: contact APIs without sending LINE messages")
    parser.add_argument("--scheduled", action="store_true", help="check: regular session only, one run per five-minute slot")
    parser.add_argument("--service", choices=["stock", "codex", "openai", "gemini", "line", "line-webhook"], help="setup/doctor: select one service")
    args = parser.parse_args()
    try:
        if args.command == "setup":
            from app.setup import setup_connections
            return setup_connections(args.service)
        settings = Settings.from_env()
        setup_logging(settings.log_level)
        if args.command == "news-sync":
            result = sync_news(settings)
            log.info("News sync | saved=%d errors=%d skipped=%s", result["saved"], result["errors"], result["skipped"])
            from app.report import write_report
            write_report(settings)
            return 1 if result["errors"] else 0
        if args.command == "review":
            result = run_portfolio_review(settings)
            log.info("Portfolio review | period=%s created=%s ai=%s", result["period"], result["created"], result["ai_used"])
            from app.report import write_report
            write_report(settings)
            return 0
        if args.command in {"refresh-history", "refresh-intraday"}:
            # Explicit historical maintenance, allowed outside market hours. No quotes/AI/LINE.
            from app.report import write_report
            if settings.mock_mode or not settings.stock_api_key:
                raise ValueError("Historical refresh requires live stock credentials")
            provider = TwelveDataProvider(settings)
            failures = 0
            with run_lock(settings.database_path.with_suffix(".lock")):
                db = Database(settings.database_path, "live")
                try:
                    for stock in settings.stocks():
                        try:
                            if args.command == "refresh-intraday":
                                bars5 = provider.fetch_intraday(stock.symbol, datetime.now(UTC))
                                db.save_intraday(stock.symbol, "twelvedata", bars5)
                                log.info("%s | 5min=%d | %s to %s UTC", stock.symbol, len(bars5), bars5[0].at, bars5[-1].end)
                                continue
                            bars = provider.fetch_history(stock.symbol, datetime.now(UTC))
                            db.save_bars(stock.symbol, "twelvedata", bars)
                            log.info("%s | history=%d | OHLC=%d | %s to %s", stock.symbol, len(bars),
                                     sum(b.open is not None and b.high is not None and b.low is not None for b in bars), bars[0].day, bars[-1].day)
                        except (DataError, ValueError, TypeError):
                            failures += 1
                            log.error("%s | history refresh failed; previous data retained", stock.symbol)
                            if args.command == "refresh-intraday":
                                break  # A missing plan entitlement must not cost nine failed requests.
                finally:
                    db.close()
            log.info("History report: %s", write_report(settings))
            return 1 if failures else 0
        if args.command == "test-codex":
            # Explicit one-off inference using synthetic data; no notifications or cooldown writes.
            from app.config import Stock
            snapshot = MockStockProvider().fetch("NVDA", datetime.now(UTC))
            indicators = calculate([bar.close for bar in snapshot.bars], snapshot.price, snapshot.previous_close)
            events = evaluate(snapshot, indicators, Stock("NVDA"), settings)
            message = CodexAnalyst(settings).summarize(analysis_payload(snapshot, indicators, events))
            output = ROOT / "data/codex-example.txt"
            output.parent.mkdir(exist_ok=True)
            output.write_text(message, encoding="utf-8")
            print(message)
            print("\nบันทึกตัวอย่าง:", output)
            return 0
        if args.command == "doctor":
            from app.setup import doctor
            return doctor(settings, args.online, args.service)
        if args.command == "report":
            from app.report import write_report
            path = write_report(settings)
            print("รายงานพร้อมแล้ว:", path)
            return 0
        if args.command in {"check", "view"}:
            from app.report import write_report
            from app.market import is_open
            if not settings.mock_mode and not is_open(datetime.now(UTC)):
                from app.close_sync import sync_close
                result = sync_close(settings)
                log.info('ตลาดปิด · ตามเก็บราคาปิด updated=%d errors=%d skipped=%d · ไม่เรียก AI/LINE', result['updated'], result['errors'], result['skipped'])
                path = write_report(settings)
                if args.command == "view" and not args.no_open:
                    webbrowser.open(path.resolve().as_uri())
                return 0
            log.info("Mode=%s | analyst=%s | notifier=%s", "MOCK" if settings.mock_mode else "LIVE",
                     settings.analyst_mode, settings.notifier_mode)
            exit_code = 0
            try:
                settings.validate_connections()
                provider = MockStockProvider(settings.history_bars) if settings.mock_mode else TwelveDataProvider(settings, market_only=True, intraday_prices=True)
                analyst = {"openai": OpenAIAnalyst, "codex": CodexAnalyst, "gemini": GeminiAnalyst}.get(settings.analyst_mode, lambda _: TemplateAnalyst())(settings)
                notifier = LineNotifier(settings) if settings.notifier_mode == "line" else ConsoleNotifier()
                exit_code = 1 if check(settings, provider, analyst, notifier, scheduled=args.scheduled)["errors"] else 0
            except ValueError:
                log.error("ตั้งค่าการเชื่อมต่อไม่ครบ · ใช้ Setup Connections.cmd หรือ doctor เพื่อตรวจ")
                exit_code = 2
            path = write_report(settings)
            log.info("รายงานกราฟ: %s", path)
            if args.command == "view" and not args.no_open:
                if not webbrowser.open(path.resolve().as_uri()):
                    log.warning("เปิดเบราว์เซอร์ไม่ได้ กรุณาเปิดไฟล์รายงานด้วยตนเอง")
            return exit_code
        db = Database(settings.database_path, "mock" if settings.mock_mode else "live")
        try:
            if args.command == "init-db":
                log.info("Database ready | %s", settings.database_path)
            else:
                for row in db.alerts(max(1, min(args.limit, 1000))):
                    print(f"{row['created_at']} | {row['status']} | {row['channel']} | attempts={row['attempts']} | {row['id']}")
                    print(row["message"] or "ยังไม่สร้างบทวิเคราะห์")
                    if row["last_error"]:
                        print("Error:", row["last_error"])
        finally:
            db.close()
        return 0
    except AnalysisError as exc:
        log.error("การวิเคราะห์ไม่สำเร็จ: %s", exc)
        return 1
    except AlreadyRunning:
        log.warning("Another check is already running; this run was skipped")
        return 0
    except (ValueError, OSError) as exc:
        # Config errors can originate from float/json parsing; do not echo raw input.
        log.error("Configuration/filesystem error (%s); check .env, watchlist and paths", type(exc).__name__)
        return 2
    except Exception as exc:
        log.error("Unexpected error (%s); inspect local configuration/database", type(exc).__name__)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
