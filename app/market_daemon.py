"""Quiet local Windows-friendly price monitor; all data/AI checks remain bounded."""

import logging
import time
from logging.handlers import RotatingFileHandler

from app.config import ROOT, Settings
from app.database import AlreadyRunning, run_lock


def configure_logging() -> None:
    (ROOT / "logs").mkdir(exist_ok=True)
    handler = RotatingFileHandler(ROOT / "logs/market-daemon.log", maxBytes=1_000_000,
                                  backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s"))
    logger = logging.getLogger("stock_manager")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.addHandler(handler)


def main() -> int:
    configure_logging()
    log = logging.getLogger("stock_manager")
    try:
        settings = Settings.from_env()
        settings.validate_connections()
    except (OSError, ValueError) as exc:
        logging.getLogger("stock_manager").error("Cannot start monitor (%s); check Setup Connections.cmd", type(exc).__name__)
        return 2
    if settings.mock_mode:
        log.info("Price monitor started in MOCK_MODE; no live market requests will be made")
    from app.analyst import CodexAnalyst, GeminiAnalyst, OpenAIAnalyst, TemplateAnalyst
    from app.fetcher import MockStockProvider, TwelveDataProvider
    from app.main import check
    from app.notifier import ConsoleNotifier, LineNotifier

    provider = MockStockProvider(settings.history_bars) if settings.mock_mode else TwelveDataProvider(
        settings, market_only=True, intraday_prices=True)
    if settings.analyst_mode == "template":
        analyst = TemplateAnalyst()
    else:
        analyst = {"codex": CodexAnalyst, "openai": OpenAIAnalyst, "gemini": GeminiAnalyst}[
            settings.analyst_mode](settings)
    notifier = LineNotifier(settings) if settings.notifier_mode == "line" else ConsoleNotifier()
    log.info("Price monitor running quietly; checks align to five-minute slots and obey market/quota guards")
    try:
        with run_lock(settings.database_path.with_suffix(".market-daemon.lock")):
            while True:
                try:
                    result = check(settings, provider, analyst, notifier, scheduled=True)
                    if result["checked"] or result["queued"] or result["sent"]:
                        from app.report import write_report
                        write_report(settings)
                except Exception as exc:
                    log.error("Price-monitor cycle failed (%s); retrying next slot", type(exc).__name__)
                # Align to wall-clock five-minute slots instead of drifting after API calls.
                wait_seconds = 300 - (int(time.time()) % 300)
                time.sleep(max(1, wait_seconds))
    except AlreadyRunning:
        log.info("Price monitor already running; duplicate start skipped")


if __name__ == "__main__":
    raise SystemExit(main())
