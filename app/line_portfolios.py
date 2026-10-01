"""Shared LINE views: cached prices, independent portfolios, one data budget."""
from dataclasses import replace
from decimal import Decimal
import logging
import sqlite3

from app.portfolio_catalog import PortfolioCatalog
from app.portfolio_scope import ScopedStore
from app.voice import thai_time

log = logging.getLogger(__name__)


def portfolio_heading(name):
    return name if name.startswith('พอร์ต') else 'พอร์ต '+name


def portfolio_overview(settings, store, now, report_reader):
    catalog = PortfolioCatalog(settings)
    value = catalog.read()
    selected = catalog.selected(value)
    lines = ['ภาพรวมทุกพอร์ตครับ', 'ติดตามพร้อมกัน '+str(len(value['portfolios']))+' พอร์ต']
    valued = []
    owned_count = 0
    unavailable = False
    for index, entry in enumerate(value['portfolios'], 1):
        lines += ['', f"{index}. {entry['name']}"]
        try:
            report = report_reader(replace(settings, portfolio_id=entry['id']), now)
        except (ValueError, OSError, KeyError, sqlite3.Error) as exc:
            unavailable = True
            log.warning('Portfolio overview unavailable for %s (%s)',entry['id'],type(exc).__name__)
            lines.append('อ่านข้อมูลพอร์ตนี้ไม่สำเร็จ ลองใหม่ภายหลังครับ')
            continue
        profile = report.get('portfolio') or {}
        live = profile.get('live') or {}
        if not profile.get('holdings'):
            lines.append(f"ติดตามอย่างเดียว {len(entry['stocks'])} หุ้น" if entry['stocks'] else 'ยังไม่มีหุ้น')
            continue
        owned_count += 1
        if not live.get('complete'):
            lines.append('รอราคา/จำนวนหุ้นครบก่อนแสดงมูลค่าพอร์ต')
            continue
        valued.append(live)
        lines.append(f"มูลค่า ${live['total_usd']:,.2f}")
        if live.get('gain_usd') is not None and live.get('gain_pct') is not None:
            lines.append(f"กำไร/ขาดทุน ${live['gain_usd']:+,.2f} ({live['gain_pct']:+.2f}%)")
        else:
            lines.append('กำไร/ขาดทุน: รอยืนยันต้นทุนรวม')
        if live.get('day_change_usd') is not None and live.get('day_change_pct') is not None:
            lines.append(f"เปลี่ยนจากปิดก่อนหน้า ${live['day_change_usd']:+,.2f} ({live['day_change_pct']:+.2f}%)")
        lines.append('ราคาอัปเดตถึง '+thai_time(live.get('oldest_quote_as_of')))
        if live.get('stale'):
            lines.append('ราคาบางตัวยังเก่า รอรอบอัปเดตครับ')
        if live.get('cost_estimated'):
            lines.append('ต้นทุนบางตัวยังเป็นค่าประมาณ')
    if owned_count and len(valued) == owned_count and not unavailable:
        total = sum((Decimal(str(v['total_usd'])) for v in valued), Decimal(0))
        lines += ['', f'มูลค่าหุ้นรวมทุกพอร์ต ${total:,.2f}']
        if all(v.get('cost_usd') is not None and v.get('gain_usd') is not None for v in valued):
            cost = sum((Decimal(str(v['cost_usd'])) for v in valued), Decimal(0))
            if cost > 0:
                gain = total-cost
                lines.append(f'กำไร/ขาดทุนรวม ${gain:+,.2f} ({gain/cost*100:+.2f}%)')
        else:
            lines.append('กำไรรวมรอยืนยันต้นทุนครบทุกพอร์ต')
        if any(v.get('stale') for v in valued):
            lines.append('ยอดรวมนี้มีราคาที่ยังเก่า')
        if len({v.get('oldest_quote_as_of') for v in valued} |
               {v.get('newest_quote_as_of') for v in valued}) > 1:
            lines.append('ใช้ราคาที่บันทึกต่างเวลากันตามเวลาใต้แต่ละพอร์ต')
    elif owned_count or unavailable:
        lines += ['', 'ยอดรวมทุกพอร์ตรอราคาครบทุกพอร์ตครับ']
    lines += ['', 'กำลังคุยรายละเอียดกับ '+selected['name']]
    if len(value['portfolios'])>1:
        lines += ['กดปุ่มชื่อพอร์ตด้านล่าง หรือพิมพ์ เลือกพอร์ต 2',
                  'สลับพอร์ตได้เลย ทุกพอร์ตยังติดตามพร้อมกัน']
    else:
        lines.append('เพิ่มพอร์ตที่สองแล้ว ระบบจะติดตามพร้อมกันให้อัตโนมัติ')
    lines.append('เพิ่มพอร์ตหรือหุ้น: เปิด Manage Portfolios.cmd บนเครื่อง')
    return '\n'.join(lines)


def dca_overview(settings, store, now, report_reader):
    from zoneinfo import ZoneInfo
    catalog = PortfolioCatalog(settings)
    cycle = now.astimezone(ZoneInfo('Asia/Bangkok')).strftime('%Y-%m')
    lines = ['แผน DCA ทุกพอร์ต · รอบ '+cycle]
    for index, entry in enumerate(catalog.read()['portfolios'], 1):
        lines += ['', f"{index}. {entry['name']}"]
        try:
            report = report_reader(replace(settings, portfolio_id=entry['id']), now)
        except (ValueError, OSError, KeyError, sqlite3.Error) as exc:
            log.warning('DCA overview unavailable for %s (%s)',entry['id'],type(exc).__name__)
            lines.append('อ่านแผน DCA ของพอร์ตนี้ไม่สำเร็จ')
            continue
        profile = report.get('portfolio') or {}
        plan = profile.get('dca') or {}
        if not plan or plan.get('enabled') is False or plan.get('monthly_total', 0) <= 0:
            lines.append('ยังไม่เปิดแผน DCA')
            continue
        lines.append(f"วันที่ {plan['day']} · {plan['monthly_total']:,.0f} {plan['currency']}/เดือน")
        updated = ScopedStore(store, entry['id']).get('dca_updated') == cycle
        lines.append('ตรวจยอดรอบนี้แล้ว' if updated else 'รออัปเดตยอดหลังซื้อสำเร็จ')
    lines += ['', 'กดชื่อพอร์ต แล้วกด อัปเดต เพื่อบันทึกยอดของพอร์ตนั้นครับ']
    return '\n'.join(lines)


def quick_replies(settings, *, dynamic=True):
    """Push shortcuts stay stable across retries; replies may show current names."""
    actions = [(text, text) for text in ('พอร์ตทั้งหมด', 'พอร์ต', 'ข่าว', 'DCA ทั้งหมด', 'เมนู')]
    if dynamic:
        catalog = PortfolioCatalog(settings)
        value = catalog.read()
        entries = value['portfolios']
        if len(entries) > 1:
            if len(entries) > 6:
                actions = [(text, text) for text in ('พอร์ตทั้งหมด', 'DCA ทั้งหมด', 'เมนู')]
            else:
                actions = [(text, text) for text in
                           ('พอร์ตทั้งหมด', 'พอร์ต', 'แผนลงทุน', 'ข่าว', 'สัปดาห์', 'DCA ทั้งหมด', 'อัปเดต')]
            selected = catalog.selected(value)['id']
            for index, p in enumerate(entries, 1):
                label = ('✓ ' if p['id'] == selected else '')+f"{index}. {p['name']}"
                label = label.encode('utf-16-le')[:40].decode('utf-16-le', errors='ignore')
                actions.append((label, 'เลือกพอร์ต '+str(index)))
        else:
            actions = [(text, text) for text in
                       ('วันนี้', 'พอร์ต', 'แผนลงทุน', 'หุ้น', 'ข่าว', 'สัปดาห์', 'เดือน', 'DCA', 'อัปเดต')]
    return {'items': [{'type': 'action', 'action': {'type': 'message', 'label': label, 'text': text}}
                      for label, text in actions]}
