"""Read-only owner commands. No brokerage access or autonomous portfolio edits."""
import json
import hashlib
import secrets
from decimal import Decimal, InvalidOperation
from datetime import UTC, datetime

from app.database import Database
from app.report import report_data
from app.voice import STYLE, thai_time, answer_text

MENU = ('อยากให้ผมดูเรื่องไหนครับ?\n\n'
        'วันนี้ — พอร์ตตอนนี้เป็นอย่างไร\n'
        'พอร์ต — มูลค่า กำไร และสัดส่วน\n'
        'แผนลงทุน — ข้อเสนอและเงื่อนไขที่ควรเพิ่ม รอ หรือทบทวน\n'
        'หุ้น NVDA — เจาะดูหุ้นทีละตัว (พิมพ์ หุ้น เพื่อดูทั้งหมด)\n\n'
        'ปิดตลาด / สัปดาห์ / เดือน — อ่านสรุปรอบล่าสุด\n'
        'ข่าว — สรุปตลาดและหุ้นทั้งพอร์ต\n'
        'ข่าว META — ข่าวของตัวที่สนใจ\n\n'
        'ติดตาม — สถานะตรวจข่าวและเอกสารใหม่\n'
        'DCA — เช็กแผนเดือนนี้\n'
        'อัปเดต — บันทึกยอดหลังซื้อ\n'
        'ถาม ตามด้วยคำถาม — คุยกับผมเรื่องพอร์ตได้เลยครับ')


def safe_text(value, maximum=4000):
    return str(value).encode('utf-16-le')[:maximum*2].decode('utf-16-le', errors='ignore')


def command(settings, store, event_id, text, now=None):
    now = now or datetime.now(UTC)
    text = text.strip()
    aliases = {'สรุป': 'วันนี้', 'สรุปวันนี้': 'วันนี้', 'หุ้น': 'หุ้นที่ถือ',
               'รีวิว': 'สัปดาห์', 'รายวัน': 'ปิดตลาด', 'รายสัปดาห์': 'สัปดาห์', 'รายเดือน': 'เดือน',
               'อัปเดต': 'อัปเดตพอร์ต', 'อัปเดตแล้ว': 'อัปเดตพอร์ตแล้ว',
               'พัก': 'พักแจ้งเตือน', 'เปิด': 'เปิดแจ้งเตือน'}
    text = aliases.get(text, text)
    if text in {'เมนู', 'ช่วยเหลือ', 'help', 'menu'}:
        return MENU
    if text == 'ถาม AI':
        return ('ถามผมได้เลยครับ พิมพ์ “ถาม” แล้วตามด้วยเรื่องที่อยากรู้ เช่น\n\n'
                'ถาม ความเสี่ยงที่ควรตรวจเพิ่มในพอร์ตตอนนี้คืออะไร\n\n'
                'ผมจะดูข้อมูลพอร์ตกับข่าวที่มี แล้วช่วยชั่งน้ำหนักทางเลือกให้ครับ')
    if text == 'ติดตาม':
        raw=store.get('research-status')
        if not raw:
            return 'กำลังรอรอบตรวจข่าวและเอกสารใหม่ครั้งแรกครับ'
        status=json.loads(raw)
        labels={'news':'ข่าวตลาดและหุ้น','filings':'เอกสาร SEC ใหม่','fundamentals':'งบรายปี'}
        lines=['สถานะการติดตามครับ','ตรวจล่าสุด '+thai_time(status['at'])]
        for kind,feeds in status['sources'].items():
            ok=sum(v in {'ok','available'} for v in feeds.values())
            lines.append(f'{labels.get(kind,kind)}: สำเร็จ {ok}/{len(feeds)} แหล่ง')
        lines+=['','ตรวจทุก 4 ชั่วโมง · ข่าว/เอกสารใหม่รวมไม่เกิน 2 ชุดต่อวัน',
                'เริ่มนับข่าวใหม่ตั้งแต่เปิดระบบติดตาม ไม่ส่งข่าวเก่าย้อนหลังเป็นข่าวด่วน',
                'สถานะ: '+('พักแจ้งเตือน' if store.get('paused')=='1' else 'เปิดติดตาม')]
        return '\n'.join(lines)
    if text in {'พักแจ้งเตือน', 'เปิดแจ้งเตือน'}:
        store.set('paused', '1' if text == 'พักแจ้งเตือน' else '0')
        return text+'สำหรับรีวิว ข่าว และ DCA แล้ว (การเตือนราคาจากระบบเดิมยังทำงาน)'
    if text == 'อัปเดตพอร์ตแล้ว':
        from zoneinfo import ZoneInfo
        store.set('dca_updated', now.astimezone(ZoneInfo('Asia/Bangkok')).strftime('%Y-%m'))
        store.set('dca_updated_date', now.astimezone(ZoneInfo('Asia/Bangkok')).date().isoformat())
        return 'บันทึกว่าคุณตรวจยอดเดือนนี้แล้ว แต่ไม่ได้เปลี่ยนจำนวนหุ้นหรือต้นทุนให้โดยอัตโนมัติ'
    if text == 'อัปเดตพอร์ต':
        return ('หลัง DCA ซื้อสำเร็จ เปิดยอดหุ้นใน Dime แล้วส่งมาแบบนี้ครับ\n\n'
                'บันทึก META จำนวนหุ้นรวม ต้นทุนรวมUSD\n\n'
                'ใช้ยอดรวมที่ถืออยู่หลังซื้อ ไม่ใช่เฉพาะที่ซื้อเพิ่ม และใช้ต้นทุน USD ไม่ใช่มูลค่าตลาดครับ\n\n'
                'ผมจะแสดงยอดเก่าเทียบยอดใหม่ให้ตรวจ ก่อนให้คุณยืนยันบันทึกทีละตัว\n'
                'อัปเดตครบแล้วพิมพ์ อัปเดตแล้ว เพื่อปิดเตือนรอบนี้ได้เลยครับ')
    if text == 'ยกเลิก':
        store.set('holding_draft', '')
        return 'ยกเลิกร่างแก้ยอดแล้ว ไม่เปลี่ยนพอร์ต'
    if text.startswith(('บันทึก ', 'ยืนยัน ')):
        return edit_holdings(settings, store, text, now)
    if text in {'ปิดตลาด', 'สัปดาห์', 'เดือน'}:
        kind = {'ปิดตลาด': 'daily', 'สัปดาห์': 'weekly', 'เดือน': 'monthly'}[text]
        saved = store.brief(kind=kind)
        if saved:
            return saved['message']+'\n\nจัดทำเมื่อ '+thai_time(datetime.fromtimestamp(saved['created'], UTC).isoformat())
        return ('ยังไม่มีสรุปรอบนี้ครับ ผมจะส่งให้หลังปิดตลาดของวันซื้อขายสุดท้ายในช่วงนั้นประมาณ 30 นาที\n\n'
                'ระหว่างนี้กด วันนี้ เพื่อดูภาพพอร์ตล่าสุดได้ครับ')
    if text == 'ข่าว':
        saved = store.brief(kind='news_weekly')
        if saved:
            return saved['message']+'\n\nจัดทำเมื่อ '+thai_time(datetime.fromtimestamp(saved['created'], UTC).isoformat())
    report = report_data(settings, now)
    if text == 'แผนลงทุน' or text.startswith('แผนลงทุน '):
        from app.decision_memo import create_memo
        return create_memo(settings, store, report, text, now)
    portfolio = report.get('portfolio') or {}
    live = portfolio.get('live', {})
    if text == 'วันนี้':
        from app.manager_brief import today_brief
        return safe_text(today_brief(report, now))
    if text == 'พอร์ต':
        if not live.get('complete'):
            return 'ราคาหรือจำนวนหุ้นยังไม่ครบ จึงไม่แสดงผลรวมบางส่วนเป็นพอร์ตทั้งหมด'
        lines = ['มาดูพอร์ตของเรากันครับ', '', f"มูลค่าหุ้นรวม ${live['total_usd']:,.2f}",
                 f"กำไร/ขาดทุนที่ยังไม่ขาย ${live['gain_usd']:+,.2f} ({live['gain_pct']:+.2f}%)", '', 'สัดส่วนตอนนี้']
        lines += [f"{h['symbol']} ${h.get('live_value_usd', 0):,.2f} · {h.get('live_weight_pct', 0):.1f}%" for h in portfolio['holdings']]
        lines += ['', 'ราคาอัปเดตถึง '+thai_time(live['oldest_quote_as_of'])]
        if live.get('stale'):
            lines.append('ราคาบางตัวยังเก่าครับ กำลังรอรอบอัปเดต')
        if live.get('cost_estimated'):
            lines.append('ต้นทุนบางตัวยังเป็นค่าประมาณ กรอกยอดจาก Dime แล้วกำไรจะตรงขึ้นครับ')
        return '\n'.join(lines)
    if text == 'หุ้นที่ถือ':
        if not portfolio.get('holdings'):
            return 'ยังไม่มีข้อมูลหุ้นในพอร์ต'
        lines = ['หุ้นของเราตอนนี้ครับ', 'เปอร์เซ็นต์ด้านล่างคือกำไร/ขาดทุนเทียบต้นทุน', '']
        for h in portfolio['holdings']:
            price = h.get('live_price')
            gain = h.get('live_gain_pct')
            lines.append(f"{h['symbol']} · ${price:,.2f}" if price is not None else f"{h['symbol']} · ยังไม่มีราคา")
            if gain is not None:
                lines[-1] += f" · {gain:+.2f}%"
            if h.get('live_stale', True):
                lines[-1] += ' · ราคายังเก่า'
        return '\n'.join(lines+['', 'อยากดูตัวไหนต่อ พิมพ์ หุ้น ตามด้วยชื่อ เช่น หุ้น NVDA ครับ'])
    symbol = text.upper().removeprefix('หุ้น ').strip()
    holding = next((h for h in portfolio.get('holdings', []) if h['symbol'] == symbol), None)
    if holding:
        price = holding.get('live_price')
        lines = [f"{symbol} ของเราครับ", f"ถืออยู่ {holding.get('quantity')} หุ้น", '']
        if price:
            lines += [f"ราคา ${price:,.2f}", f"กำไร/ขาดทุนเทียบต้นทุน {holding['live_gain_pct']:+.2f}%"]
        else:
            lines.append('ยังไม่มีราคาล่าสุดครับ')
        lines += ['', 'ราคา ณ '+thai_time(holding.get('live_as_of'))]
        if holding.get('live_stale', True):
            lines.append('ราคานี้ยังเก่าครับ')
        stock = next((s for s in report.get('stocks', []) if s['symbol'] == symbol), {})
        if stock.get('signals') and not stock.get('stale', True):
            lines += ['', 'มีจุดที่ควรดูต่อ: '+' / '.join(stock['signals'][:2])]
        lines += ['', f'พิมพ์ ข่าว {symbol} เพื่ออ่านข่าว หรือ ถาม {symbol} ควรจับตาอะไร เพื่อวิเคราะห์ต่อครับ']
        return '\n'.join(lines)
    if text.startswith('ข่าว'):
        wanted = text.removeprefix('ข่าว').strip().upper()
        items = [n for n in report['news'] if not wanted or n['symbol'] == wanted][:5]
        if not items:
            return 'รอบนี้ยังไม่มีข่าวที่คัดไว้ครับ สรุปตลาดและหุ้นทั้ง 9 ตัวจะส่งทุกวันจันทร์ 08:00 น.'
        from app.web_news import news_reference
        entries = []
        for item in items:
            excerpt = str(item.get('excerpt', '')).strip()[:240]
            entries.append(f"{item['symbol']} · {thai_time(item['published_at'])}\n{item['title']}"
                           +(f"\n{excerpt}" if excerpt else '')
                           +'\n'+news_reference(item))
        return ('หัวข้อข่าวที่คัดไว้ครับ กดปุ่มด้านล่างเพื่อเปิดแหล่งอ้างอิง\n\n'
                +'\n\n'.join(entries))
    if text.upper() == 'DCA':
        plan = portfolio.get('dca')
        if not plan:
            return 'ยังไม่มีแผน DCA ที่บันทึก'
        from zoneinfo import ZoneInfo
        cycle = now.astimezone(ZoneInfo('Asia/Bangkok')).strftime('%Y-%m')
        updated = store.get('dca_updated') == cycle
        status = 'บันทึกว่าตรวจยอดรอบนี้แล้ว' if updated else 'ยังไม่บันทึกการอัปเดตยอดรอบนี้'
        reconciled = portfolio.get('holdings_as_of')
        last_totals = f"\nยอดหุ้นอัปเดตล่าสุด {thai_time(reconciled)}" if reconciled else ''
        return (f"DCA เดือนนี้ตามแผนเดิมครับ\n\n"
                f"วันที่ {plan['day']} · {plan['monthly_total']:,.0f} {plan['currency']}\n"
                f"แบ่งเท่ากันตัวละ {plan['per_stock']:,.0f} {plan['currency']}\n\n"
                f"รอบ {cycle}: {status}{last_totals}\n\n"
                'ผมจะเตือนให้เช็ก Auto DCA ใน Dime และอัปเดตยอดหลังซื้อครับ '
                'ถ้าตรงวันหยุด ให้ดูวันส่งคำสั่งจริงในแอปอีกที\n\n'
                'ซื้อสำเร็จแล้วกด อัปเดต ได้เลยครับ')
    if text == 'สถานะ':
        ai = report['ai']
        return (f"ตลาด {'เปิด' if report['market']['open'] else 'ปิด/นอกปฏิทิน'}\n"
                f"เครดิตราคาที่ระบบนับวันนี้ (UTC): {report['market']['used']}/{report['market']['limit']}\n"
                f"AI วันนี้ (UTC): {ai['attempts_today']}/{ai['daily_limit']} ครั้ง\n"
                f"ข่าวที่บันทึก: {len(report['news'])} รายการ (ไม่รับรองว่าครบทุกข่าว)\n"
                f"เตือนผู้จัดการ: {'พัก' if store.get('paused') == '1' else 'เปิด'}\n"
                'รับคำสั่งได้เมื่อเครื่อง บอต และ tunnel ทำงานพร้อมกัน')
    if not text.startswith('ถาม '):
        return 'ใช้ “ถาม ตามด้วยคำถาม” เมื่อต้องการให้ AI วิเคราะห์ใหม่ครับ\n\n'+MENU
    if settings.mock_mode or settings.analyst_mode != 'codex':
        return 'ยังไม่ได้เปิด Codex สำหรับวิเคราะห์คำถาม ข้อมูลทั่วไปใช้เมนูได้โดยไม่เรียก AI'
    db = Database(settings.database_path, 'live')
    try:
        allowed = db.reserve_ai('line:'+event_id, '__line_chat__', now,
                                settings.ai_max_calls_per_day, settings.ai_max_calls_per_day)
    finally:
        db.close()
    if not allowed:
        return 'ถึงเพดาน AI วันนี้ หรือคำถามนี้เคยถูกประมวลผลแล้ว ใช้ พอร์ต / ข่าว / รีวิว ได้โดยไม่เรียก AI'
    from app.codex_client import analyze, CodexError
    from app.review import review_payload
    payload = review_payload(portfolio, report['news']) if portfolio else {'limitations': 'No portfolio data'}
    prompt = (STYLE+'\ncheck_more ตอบคำถามตรง ๆ อ้างอิงข่าวด้วยชื่อสำนักและวันที่เฉพาะที่ใช้: '
              + json.dumps({'question': text[4:][:2000], 'data': payload}, ensure_ascii=False))
    try:
        answer = analyze(settings, prompt)
        return answer_text(answer)
    except CodexError:
        return 'วิเคราะห์ไม่สำเร็จ นับเป็นหนึ่งความพยายามในโควตาแล้ว ระบบจะไม่เรียก AI ซ้ำเอง กรุณาลองภายหลัง'


def edit_holdings(settings, store, text, now):
    """Explicit, expiring confirmation; broker TOTALS only, no implied trades."""
    if settings.mock_mode:
        return 'โหมดจำลองไม่แก้ยอดพอร์ตจริง'
    path = settings.database_path.parent/'portfolio-profile.json'
    if text.startswith('ยืนยัน '):
        raw = store.get('holding_draft')
        if not raw:
            return 'ไม่มีร่างรอยืนยัน'
        draft = json.loads(raw)
        if now.timestamp() > draft['expires']:
            store.set('holding_draft', '')
            return 'ร่างหมดอายุแล้ว กรุณาสร้างใหม่'
        if not secrets.compare_digest(text[7:].strip(), draft['code']):
            return 'รหัสยืนยันไม่ตรง ไม่มีการแก้ไข'
        from app.portfolio_editor import save_holdings
        try:
            save_holdings(path, draft['changes'], now, expected_digest=draft['digest'])
        except ValueError:
            store.set('holding_draft', '')
            return 'ข้อมูลพอร์ตเปลี่ยนหรือข้อมูลไม่ถูกต้อง ไม่มีการแก้ไข กรุณาสร้างร่างใหม่'
        store.set('holding_draft', '')
        from app.report import write_report
        try:
            write_report(settings, now)
        except Exception:
            return 'บันทึกยอดแล้วและมีสำรองก่อนแก้ แต่สร้างหน้าเว็บใหม่ไม่สำเร็จ กรุณาเปิดรายงานอีกครั้ง'
        return 'บันทึกยอดรวมและสร้างรายงานใหม่แล้ว มีไฟล์สำรองก่อนแก้ไข · หากเว้นต้นทุน ระบบจะรอยืนยันกำไรจาก Dime · ไม่ได้ส่งคำสั่งซื้อขาย'
    parts = text.split()
    if len(parts) not in {3, 4}:
        return 'รูปแบบ: บันทึก META จำนวนหุ้นรวม [ต้นทุนรวมUSD]\nต้นทุนรวมเป็นข้อมูลเสริม: ถ้าเว้นไว้ ระบบเก็บยอดหุ้นหลัง DCA ได้ แต่จะยังไม่ยืนยันกำไรจริง'
    symbol = parts[1].upper()
    try:
        quantity = Decimal(parts[2])
        cost = Decimal(parts[3]) if len(parts) == 4 else None
        if (not quantity.is_finite() or cost is not None and not cost.is_finite()
                or not 0 < quantity < Decimal('1e12') or cost is not None and not Decimal('.01') <= cost < Decimal('1e15')):
            raise ValueError()
    except (ValueError, InvalidOperation):
        return 'จำนวนหุ้นต้องเป็นบวก และหากใส่ต้นทุนรวม USD ต้องไม่น้อยกว่า 0.01'
    raw = path.read_text(encoding='utf-8')
    profile = json.loads(raw)
    holding = next((h for h in profile['holdings'] if h['symbol'] == symbol), None)
    if not holding:
        return 'แก้ได้เฉพาะหุ้นที่มีอยู่ในพอร์ตเท่านั้น'
    changes = {h['symbol']: {'quantity': h['quantity'], 'cost_basis_usd': h.get('cost_basis_usd')}
               for h in profile['holdings']}
    changes[symbol] = {'quantity': str(quantity), 'cost_basis_usd': str(cost) if cost is not None else ''}
    code = secrets.token_hex(3).upper()
    store.set('holding_draft', json.dumps({'changes': changes, 'code': code, 'expires': now.timestamp()+600,
                                         'digest': hashlib.sha256(raw.encode()).hexdigest()}))
    cost_line = (f"ต้นทุนรวมใหม่: ${cost} USD (ไม่ใช่มูลค่าตลาด)\n" if cost is not None else
                 'ต้นทุนรวม: ยังไม่ยืนยัน · ระบบจะหยุดใช้ตัวเลขกำไรจนกว่าจะกรอกจาก Dime\n')
    return (f"ร่างแก้ {symbol}\nจำนวนหุ้นรวม: {holding['quantity']} → {quantity}\n"
            + cost_line
            + f"ยังไม่ได้บันทึก ตรวจยอดแล้วพิมพ์ ยืนยัน {code} ภายใน 10 นาที หรือ ยกเลิก\nไม่ใช่คำสั่งซื้อขาย")
