"""Offline regular-session calendar. Fail closed outside the verified years."""
from datetime import datetime, time, timedelta, UTC
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
# NYSE published calendar; see README for source. Extraordinary closures require updates.
HOLIDAYS = {
    2026: "01-01 01-19 02-16 04-03 05-25 06-19 07-03 09-07 11-26 12-25",
    2027: "01-01 01-18 02-15 03-26 05-31 06-18 07-05 09-06 11-25 12-24",
    2028: "01-17 02-21 04-14 05-29 06-19 07-04 09-04 11-23 12-25",
}
EARLY = {"2026-11-27", "2026-12-24", "2027-11-26", "2028-07-03", "2028-11-24"}


def session_close(day):
    return datetime.combine(day, time(13 if day.isoformat() in EARLY else 16), NY).astimezone(UTC)


def completed_session(now):
    """Most recent regular session closed at least 15 minutes ago; fail closed."""
    if now.tzinfo is None:
        raise ValueError('Aware time required')
    day = now.astimezone(NY).date()
    if day.year not in HOLIDAYS:
        return None
    for _ in range(14):
        if (is_open(datetime.combine(day, time(10), NY))
                and session_close(day)+timedelta(minutes=15) <= now):
            return day
        day -= timedelta(days=1)
    return None


def is_open(now: datetime) -> bool:
    if now.tzinfo is None:
        raise ValueError("Market time must be timezone-aware")
    local = now.astimezone(NY)
    if local.year not in HOLIDAYS or local.weekday() >= 5:
        return False
    if local.strftime("%m-%d") in HOLIDAYS[local.year].split():
        return False
    close = time(13) if local.date().isoformat() in EARLY else time(16)
    return time(9, 30) <= local.time() < close
