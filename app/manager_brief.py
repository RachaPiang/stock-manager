"""Deterministic, low-noise portfolio-manager style LINE briefs.

These functions only read the saved report.  They neither fetch a price nor call
AI, so a useful briefing cannot accidentally consume market-data or model quota.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

BANGKOK = ZoneInfo('Asia/Bangkok')


def _money(value: object) -> str:
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "ข้อมูลไม่ครบ"


def _pct(value: object) -> str:
    try:
        return f"{float(value):+.2f}%"
    except (TypeError, ValueError):
        return "ข้อมูลไม่ครบ"


def _holding_day_pct(holding: dict) -> float | None:
    try:
        price = float(holding['live_price'])
        previous = float(holding['live_previous_value_usd']) / float(holding['quantity'])
        return (price / previous - 1) * 100 if previous > 0 else None
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None


def _freshness(portfolio: dict) -> str:
    live = portfolio.get('live', {})
    if not live.get('complete'):
        return 'ราคา/จำนวนหุ้นยังไม่ครบ จึงเป็นภาพพอร์ตไม่สมบูรณ์'
    if live.get('stale'):
        return 'มีราคาบางตัวเก่า ควรอ่านเวลาราคาก่อนใช้ประกอบการตัดสินใจ'
    from app.voice import thai_time
    return 'ราคาอัปเดตถึง '+thai_time(live.get('oldest_quote_as_of'))


def today_brief(report: dict, now: datetime) -> str:
    """A compact current-state brief, intentionally not an investment instruction."""
    portfolio = report.get('portfolio') or {}
    live = portfolio.get('live') or {}
    local_time = now.astimezone(BANGKOK).strftime('%d/%m %H:%M')
    lines = [f'พอร์ตตอนนี้ครับ · {local_time} น.', '']
    if not live.get('complete'):
        return '\n'.join(lines + ['ยังสร้างภาพรวมพอร์ตไม่ได้ครบ', _freshness(portfolio),
                                   'สิ่งที่ทำได้: ตรวจยอดจริงหลัง DCA แล้วใช้ อัปเดตพอร์ต'])
    lines += [f"มูลค่า {_money(live.get('total_usd'))}", f"กำไร/ขาดทุนที่ยังไม่ขาย {_money(live.get('gain_usd'))} ({_pct(live.get('gain_pct'))})"]
    if live.get('day_change_pct') is not None:
        lines.append(f"วันนี้เทียบราคาปิดก่อนหน้า: {_money(live.get('day_change_usd'))} ({_pct(live.get('day_change_pct'))})")
    movements = []
    for holding in portfolio.get('holdings', []):
        change = _holding_day_pct(holding)
        if change is not None:
            movements.append((abs(change), holding.get('symbol', '?'), change))
    if movements:
        movements.sort(reverse=True)
        lines += ['', 'ตัวที่ขยับเด่นวันนี้']
        lines += [f'{symbol}  {_pct(change)}' for _, symbol, change in movements[:3]]
    signals = []
    for stock in report.get('stocks', []):
        if stock.get('stale'):
            continue
        for signal in stock.get('signals') or []:
            signals.append(f"{stock['symbol']} — {signal}")
    if signals:
        lines += ['', 'จุดที่น่าดูต่อ']+signals[:3]
    latest = (report.get('news') or [])[:1]
    if latest:
        item = latest[0]
        lines += ['', f"ข่าวที่คัดไว้: {item['symbol']} — {item['title']}", 'กด ข่าว เพื่ออ่านแหล่งที่มาครับ']
    lines += ['', _freshness(portfolio)]
    return '\n'.join(lines)


def news_digest(items: list[dict]) -> str:
    """Only source-linked, already-classified company announcements are included."""
    selected = [item for item in items if item.get('source_url')][:3]
    if not selected:
        return ''
    lines = ['ข่าวสำคัญที่เพิ่มเข้าระบบ · ตรวจแหล่งต้นฉบับก่อนสรุปผลต่อพอร์ต']
    for item in selected:
        lines += [f"{item['symbol']} · {item['title']}",
                  f"เหตุผลที่คัด: {item.get('reason', 'ไม่ระบุ')}", item['source_url']]
    lines.append('ข่าวเดียวไม่ยืนยันว่าราคาควรขึ้นหรือลง · พิมพ์ ข่าว หรือ ถาม … เพื่อดู/วิเคราะห์ต่อ')
    return '\n'.join(lines)
