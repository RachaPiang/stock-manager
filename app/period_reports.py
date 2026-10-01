"""Exact close-to-close periods, with explicit fixed-holdings attribution."""
from calendar import monthrange
from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.market import NY, HOLIDAYS, is_open, session_close, completed_session

BANGKOK = ZoneInfo('Asia/Bangkok')


def delivery_time(close, hour=10):
    """Send at the owner's Thai time, after the session has actually closed."""
    closed = close + timedelta(minutes=30)
    local_day = closed.astimezone(BANGKOK).date()
    return max(closed, datetime.combine(local_day, time(hour), BANGKOK))


def session_on_or_before(day):
    for _ in range(15):
        if day.year not in HOLIDAYS:
            return None
        if is_open(datetime.combine(day, time(10), NY)):
            return day
        day -= timedelta(days=1)
    return None


def due_periods(now, delivery_hour=10, catchup_days=7):
    """Latest only; allow catch-up without replaying months of old messages."""
    last = completed_session(now)
    if last is None:
        return []
    result = []
    def add(kind, start, end, max_age):
        if not start or not end:
            return
        closed = session_close(end) + timedelta(minutes=30)
        ready = delivery_time(session_close(end), delivery_hour)
        if ready <= now < ready + max_age:
            result.append(dict(kind=kind, start=start.isoformat(), end=end.isoformat(),
                               key=f'{kind}:{end}', ready=ready, closed=closed))
    # Before today's scheduled time, the previous unsent report may still be due.
    daily_end = last
    while daily_end and delivery_time(session_close(daily_end), delivery_hour) > now:
        daily_end = session_on_or_before(daily_end-timedelta(days=1))
    if daily_end:
        add('daily', session_on_or_before(daily_end-timedelta(days=1)), daily_end,
            timedelta(days=catchup_days))
    monday = last-timedelta(days=last.weekday())
    for week in (monday, monday-timedelta(days=7)):
        end = session_on_or_before(week+timedelta(days=4))
        if end and delivery_time(session_close(end), delivery_hour) <= now:
            add('weekly', session_on_or_before(week-timedelta(days=1)), end, timedelta(days=14))
            break
    first = last.replace(day=1)
    for month in (first, (first-timedelta(days=1)).replace(day=1)):
        end = session_on_or_before(month.replace(day=monthrange(month.year, month.month)[1]))
        if end and delivery_time(session_close(end), delivery_hour) <= now:
            add('monthly', session_on_or_before(month-timedelta(days=1)), end, timedelta(days=40))
            break
    return result


def period_numbers(report, period):
    """Never substitute a nearby date for a missing baseline or closing bar."""
    profile = report.get('portfolio') or {}
    stocks = {s['symbol']: s for s in report.get('stocks', [])}
    rows, missing = [], []
    for holding in profile.get('holdings', []):
        symbol = holding['symbol']
        bars = {b['day']: b['close'] for b in stocks.get(symbol, {}).get('bars', [])}
        try:
            before, after = Decimal(str(bars[period['start']])), Decimal(str(bars[period['end']]))
            quantity = Decimal(str(holding['quantity']))
            if any(not v.is_finite() or v <= 0 for v in (before, after, quantity)):
                raise ValueError()
        except (KeyError, TypeError, ValueError, ArithmeticError):
            missing.append(symbol)
            continue
        rows.append(dict(symbol=symbol, close=float(after), change_pct=float((after/before-1)*100),
                         before_value=float(quantity*before), after_value=float(quantity*after),
                         contribution_usd=float(quantity*(after-before))))
    complete = bool(rows) and not missing
    start_total = sum(Decimal(str(r['before_value'])) for r in rows)
    end_total = sum(Decimal(str(r['after_value'])) for r in rows)
    return dict(start=period['start'], end=period['end'], rows=rows, missing=missing,
                complete=complete, end_value=float(end_total) if complete else None,
                change_usd=float(end_total-start_total) if complete else None,
                change_pct=float((end_total/start_total-1)*100) if complete else None,
                basis='price movement with current quantities held fixed; excludes DCA flows, dividends, fees and FX')


def render_period(numbers, kind):
    label = {'daily': 'สรุปหลังปิดตลาด', 'weekly': 'สรุปสัปดาห์นี้', 'monthly': 'สรุปเดือนนี้'}[kind]
    lines = [f"{label}ครับ", f"ราคาปิดสหรัฐ {numbers['start']} → {numbers['end']}"]
    if numbers['complete']:
        lines += ['', f"มูลค่าหุ้นตามจำนวนที่ถือ ${numbers['end_value']:,.2f}",
                  f"ผลจากราคา {numbers['change_usd']:+,.2f} USD ({numbers['change_pct']:+.2f}%)"]
    else:
        lines += ['', 'ราคาปิดยังไม่ครบ: '+', '.join(numbers['missing'])+'. ผมยังไม่รวมเป็นยอดพอร์ตครับ']
    lines += ['', 'หุ้นในพอร์ต']
    for r in numbers['rows']:
        lines.append(f"{r['symbol']}  ${r['close']:,.2f}  ({r['change_pct']:+.2f}%)")
    if numbers['complete']:
        movers = sorted(numbers['rows'], key=lambda r: abs(r['contribution_usd']), reverse=True)[:2]
        lines += ['', 'ตัวที่มีผลต่อพอร์ตมากที่สุด: '+', '.join(
            f"{r['symbol']} {r['contribution_usd']:+,.2f} USD" for r in movers)]
    lines += ['', 'เทียบด้วยจำนวนหุ้นปัจจุบันคงที่ จึงแยกเงิน DCA ออกจากผลของราคา; ไม่รวมปันผล ค่าธรรมเนียม และค่าเงิน']
    return '\n'.join(lines)
