"""Private, self-contained HTML report. No server, CDN, credentials or HTTP calls."""

import json
import sqlite3
import tempfile
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from app.config import ROOT, Settings, load_watchlist
from app.fetcher import DailyBar, DataError, Snapshot, NY
from app.indicators import calculate
from app.rules import evaluate
from app.market import is_open, EARLY, completed_session, session_close
from app.market_state import DAILY_LIMIT
from app.intraday import previous_session_day

NAMES = {"META": "Meta Platforms", "GOOGL": "Alphabet", "ETN": "Eaton",
         "NVDA": "NVIDIA", "MSFT": "Microsoft", "TXN": "Texas Instruments",
         "ASML": "ASML Holding", "AMZN": "Amazon", "V": "Visa"}


def connection_status(settings: Settings) -> list[dict]:
    from app.codex_client import find_codex
    use_codex = settings.configured_analyst_mode == "codex"
    return [
        {"name": "ราคาหุ้น · Twelve Data", "configured": bool(settings.stock_api_key),
         "active": not settings.mock_mode, "description": "รอ API key" if not settings.stock_api_key else "ใส่ key แล้ว · ยังไม่ใช่ผลยืนยันการเชื่อมต่อ"},
        {"name": "บทวิเคราะห์ · Codex ในเครื่อง" if use_codex else "บทวิเคราะห์ · OpenAI API",
         "configured": bool(find_codex(settings.codex_cli_path)) if use_codex else bool(settings.openai_api_key and settings.openai_model),
         "active": not settings.mock_mode and settings.analyst_mode in {"codex", "openai"},
         "description": ("เลือก Codex แล้ว · โหมดจำลองยังใช้แม่แบบ" if settings.mock_mode else "ใช้ ChatGPT login ของ Codex · ต้องต่ออินเทอร์เน็ต") if use_codex else ("ใช้แม่แบบในเครื่อง" if settings.analyst_mode != "openai" else "เปิดใช้ AI เมื่อเกิดเหตุการณ์")},
        {"name": "แจ้งเตือน · LINE", "configured": bool(settings.line_token and settings.line_user_id),
         "active": not settings.mock_mode and settings.notifier_mode == "line",
         "description": "บันทึกในเครื่อง" if settings.notifier_mode != "line" else "เปิดส่งข้อความเมื่อเกิดเหตุการณ์"},
        {"name": "ข่าว · ประกาศบริษัท", "configured": settings.news_mode == "press_releases",
         "active": not settings.mock_mode and settings.news_mode == "press_releases",
         "description": "ดึงเมื่อสั่ง news-sync · ไม่ทำงานในรอบตรวจราคา 5 นาที"},
    ]


def report_data(settings: Settings, now: datetime | None = None) -> dict:
    """Read only the selected database. Never fill a live report with mock prices."""
    now = now or datetime.now(UTC)
    source = "mock" if settings.mock_mode else settings.stock_provider
    result = {"mock": settings.mock_mode, "generated_at": now.isoformat(), "stocks": [],
              "last_run": None, "connections": connection_status(settings)}
    result["ai"] = {"attempts_today": 0, "daily_limit": settings.ai_max_calls_per_day,
                    "stock_limit": settings.ai_max_calls_per_stock_per_day}
    from app.portfolio import attach_live_valuation, load_portfolio, portfolio_history, holdings_version
    result["portfolio"] = None if settings.mock_mode else load_portfolio(settings.database_path.parent, settings.portfolio_id)
    from app.portfolio_catalog import PortfolioCatalog
    from app.budget_planner import debug_usage
    catalog = PortfolioCatalog(settings)
    result['portfolio_info'] = catalog.selected()
    result['portfolio_catalog'] = [dict(id=p['id'], name=p['name'], active=p['id']==catalog.read()['active_id'],
        url=settings.database_path.stem+'-portfolio-'+p['id']+'.html') for p in catalog.read()['portfolios']]
    result['budget_plan'] = debug_usage(settings, now)
    result["news"], result["portfolio_review"], result["portfolio_history"] = [], None, []
    result['portfolio_intraday'] = []
    result['portfolio_observations'] = []
    result["market"] = {"open": is_open(now), "interval_minutes": 5, "expected_full_day": result['budget_plan']['planned_credits'],
                        "limit": DAILY_LIMIT, "used": 0, "utc_day": now.astimezone(UTC).date().isoformat()}
    usage_path = settings.database_path.parent / "market-api.sqlite3"
    if usage_path.exists() and not settings.mock_mode:
        usage_db = sqlite3.connect(usage_path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            start = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
            result["market"]["used"] = usage_db.execute("SELECT count(*) FROM requests WHERE at>=?", (start,)).fetchone()[0]
        finally:
            usage_db.close()
    connection = None
    if settings.database_path.exists():
        connection = sqlite3.connect(settings.database_path.resolve().as_uri() + "?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute('BEGIN')  # One consistent read while a scheduled check writes prices.
    try:
        has_intraday = bool(connection and connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='intraday'").fetchone())
        if connection:
            if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ai_calls'").fetchone():
                result["ai"]["attempts_today"] = connection.execute("SELECT count(*) FROM ai_calls WHERE day=?", (now.astimezone(UTC).date().isoformat(),)).fetchone()[0]
            stored = connection.execute("SELECT value FROM metadata WHERE key='mode'").fetchone()
            if not stored or stored[0] != ("mock" if settings.mock_mode else "live"):
                raise ValueError("Database mode mismatch")
            row = connection.execute("SELECT started_at, finished_at, status, checked, errors FROM runs ORDER BY id DESC LIMIT 1").fetchone()
            result["last_run"] = dict(row) if row else None
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "news_items" in tables:
                result["news"] = [dict(row) for row in connection.execute("""SELECT source_id,symbol,published_at,
                    title,excerpt,importance,reason,source_name,source_url,fetched_at FROM news_items
                    ORDER BY published_at DESC LIMIT 30""")]
                from app.web_news import significant
                result['news'] = [n for n in result['news'] if not n['source_id'].startswith('web:') or significant(n['title'])]
            if "portfolio_reviews" in tables:
                review = connection.execute("""SELECT period,created_at,message,source_count,ai_used
                    FROM portfolio_reviews WHERE period LIKE ? ORDER BY created_at DESC LIMIT 1""",
                    ('%:portfolio:'+result['portfolio_info']['id'] if result['portfolio_info']['id']!='main' else '____-W__',)).fetchone()
                result["portfolio_review"] = dict(review) if review else None
            if 'portfolio_observations' in tables and result['portfolio']:
                result['portfolio_observations'] = [dict(r) for r in connection.execute(
                    'SELECT at AS day, value_usd, cost_usd FROM portfolio_observations WHERE basis=? ORDER BY at',
                    (holdings_version(result['portfolio']),))]
        for stock in settings.stocks(selected=True):
            name = next((s.get('name') for s in result['portfolio_info']['stocks'] if s['symbol']==stock.symbol), None)
            item = {"symbol": stock.symbol, "name": name or NAMES.get(stock.symbol, stock.symbol), "bars": [],
                    "intraday": [], "intraday_previous": {}, "price_kind": "quote",
                    "price": None, "as_of": None, "previous_close": None, "indicators": None, "signals": [],
                    "stale": True, "note": "ยังไม่มีข้อมูล · เปิด Open Portfolio.cmd เพื่อตรวจหุ้น",
                    "target_price": stock.target_price, "alert": None, "source": source}
            from app.fundamentals import CIKS
            item['research_coverage'] = dict(sec='supported' if stock.symbol in CIKS else 'unsupported',
                news='company_headlines' if stock.symbol in NAMES else 'ticker_headlines_only')
            result["stocks"].append(item)
            from app.investment_notes import compact_notes
            item['investment_notes'] = compact_notes(next((s.get('investment_notes') for s in
                result['portfolio_info']['stocks'] if s['symbol'] == stock.symbol), None))
            if not connection:
                continue
            if has_intraday:
                intrarows = connection.execute("SELECT at,open,high,low,close FROM intraday WHERE symbol=? AND source=? ORDER BY at DESC LIMIT 1000", (stock.symbol, source)).fetchall()
                item["intraday"] = [{**dict(r), "day": datetime.fromisoformat(r["at"]).astimezone(NY).date().isoformat()} for r in reversed(intrarows)]
                for bar in item["intraday"]:
                    bar["session_end"] = datetime.fromisoformat(bar["day"]).replace(hour=13 if bar["day"] in EARLY else 16, tzinfo=NY).astimezone(UTC).isoformat()
            quote = connection.execute("""SELECT * FROM quotes
                WHERE symbol=? AND source=? ORDER BY as_of DESC LIMIT 1""", (stock.symbol, source)).fetchone()
            completed = completed_session(now)
            rows = connection.execute("""SELECT * FROM prices WHERE symbol=? AND source=? AND day<=?
                ORDER BY day DESC LIMIT ?""", (stock.symbol, source, now.astimezone(NY).date().isoformat(), settings.history_bars)).fetchall()
            rows = [r for r in rows if completed and r['day'] <= completed.isoformat()]
            bars = tuple(DailyBar(date.fromisoformat(r["day"]), r["close"],
                                 *(r[key] if key in r.keys() else None for key in ("open", "high", "low")))
                         for r in reversed(rows))
            item["bars"] = [{"day": b.day.isoformat(), "close": b.close, "open": b.open, "high": b.high, "low": b.low} for b in bars]
            close_by_day = {b.day: b.close for b in bars}
            for session_day in {b["day"] for b in item["intraday"]}:
                try:
                    prior = previous_session_day(datetime.fromisoformat(session_day).replace(tzinfo=NY))
                    item["intraday_previous"][session_day] = close_by_day.get(prior)
                except ValueError:
                    item["intraday_previous"][session_day] = None
            # Use the newest completed, saved five-minute bar consistently across portfolio and stock UI.
            if item['intraday']:
                b = item['intraday'][-1]
                end = datetime.fromisoformat(b['at']) + timedelta(minutes=5)
                if end <= now and (not quote or end > datetime.fromisoformat(quote['as_of'])):
                    quote = dict(price=b['close'], as_of=end.isoformat(), price_kind='5min_close',
                                 previous_close=item['intraday_previous'].get(b['day']))
            if not quote:
                continue
            item["price_kind"] = quote["price_kind"] if "price_kind" in quote.keys() else "quote"
            snapshot = Snapshot(stock.symbol, quote["price"], quote["previous_close"],
                                datetime.fromisoformat(quote["as_of"]), bars, source, simulated=settings.mock_mode)
            item.update(price=snapshot.price, as_of=snapshot.as_of.isoformat(),
                        previous_close=snapshot.previous_close,
                        bars=[{"day": b.day.isoformat(), "close": b.close, "open": b.open,
                               "high": b.high, "low": b.low} for b in bars])
            try:
                snapshot.validate(now, settings.max_quote_age_hours)
            except DataError:
                item["note"] = "ข้อมูลเก่าหรือไม่สมบูรณ์ · แสดงข้อมูลที่เก็บไว้ ไม่ประเมินสัญญาณใหม่"
            else:
                indicators = calculate([b.close for b in bars], snapshot.price, snapshot.previous_close, stock.target_price)
                item.update(stale=False, note="", indicators=indicators.to_dict(),
                            signals=[e.title for e in evaluate(snapshot, indicators, stock, settings)])
            if completed and snapshot.as_of < session_close(completed):
                item.update(stale=True, note='ข้อมูลยังไม่ถึงราคาปิดรอบล่าสุด · รอตามเก็บหลังเปิดเครื่อง')
            elif is_open(now) and now-snapshot.as_of > timedelta(minutes=15):
                item.update(stale=True, note='ข้อมูลขาดช่วงระหว่างตลาดเปิด · ตรวจรอบอัปเดต')
            alert = connection.execute("""SELECT n.created_at, n.status, n.message, n.channel
                FROM notifications n WHERE n.id IN (SELECT notification_id FROM events WHERE symbol=?)
                ORDER BY n.created_at DESC LIMIT 1""", (stock.symbol,)).fetchone()
            if alert:
                item["alert"] = dict(alert)
        if result["portfolio"]:
            attach_live_valuation(result["portfolio"], {
                item['symbol']: {**{k: item[k] for k in ('price', 'as_of', 'previous_close', 'price_kind')},
                                 'stale': item['stale'] or bool(result['market']['open'] and item['as_of']
                                     and now - datetime.fromisoformat(item['as_of']) > timedelta(minutes=15))}
                for item in result["stocks"]
            })
            result["portfolio_history"] = portfolio_history(result["portfolio"], result["stocks"])
            result['portfolio_intraday'] = portfolio_history(result['portfolio'], result['stocks'], intraday=True)
    finally:
        if connection:
            connection.close()
    symbols = {s['symbol'] for s in result['stocks']}
    result['news'] = [n for n in result['news'] if n['symbol'] in symbols or n['symbol']=='MARKET']
    from app.benchmark import report_comparison
    result['benchmark'] = report_comparison(settings, result['portfolio_history'])
    return result


def write_report(settings: Settings, now: datetime | None = None, *, _single=False) -> Path:
    if not _single and not settings.mock_mode:
        from app.portfolio_catalog import PortfolioCatalog
        for p in PortfolioCatalog(settings).read()['portfolios']:
            write_report(replace(settings, portfolio_id=p['id']), now, _single=True)
    data = report_data(settings, now)
    if data.get('portfolio') and not settings.mock_mode and settings.database_path.exists():
        from app.portfolio_tracking import record_observations
        data['portfolio_observations'] = record_observations(settings.database_path, data)
    payload = json.dumps(data, ensure_ascii=False, allow_nan=False)
    # Keep untrusted vendor/model text inside JSON, never executable HTML/script.
    payload = payload.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    payload = payload.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    template = (ROOT / "app/templates/portfolio.html").read_text(encoding="utf-8")
    template = template.replace('__PORTFOLIO_DASHBOARD_JS__', (ROOT / 'app/templates/portfolio-dashboard.js').read_text(encoding='utf-8'))
    suffix = ('-'+data['portfolio_info']['id']) if _single else ''
    output = settings.database_path.parent / (settings.database_path.stem + "-portfolio"+suffix+".html")
    output.parent.mkdir(parents=True, exist_ok=True)
    # A browser must see the previous complete report or the next complete report.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent, suffix=".html.tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(template.replace("__PORTFOLIO_JSON__", payload))
        temporary.replace(output)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
    return output
