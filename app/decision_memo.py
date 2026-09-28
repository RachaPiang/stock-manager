"""On-demand portfolio decision memo; bounded, cached and source-labelled."""
import hashlib
import json
import re
from datetime import UTC, datetime, timedelta

from app.review import review_payload
from app.voice import thai_time


def create_memo(settings, store, report, text, now):
    portfolio = report.get('portfolio')
    if not portfolio:
        return 'ยังไม่มีข้อมูลพอร์ตสำหรับจัดแผนลงทุนครับ'
    parts = text.split()
    budget = None
    if len(parts) > 1:
        if len(parts) != 2 or not re.fullmatch(r'\d{1,8}(?:\.\d{1,2})?', parts[1]) or float(parts[1]) <= 0:
            return 'พิมพ์ แผนลงทุน หรือ แผนลงทุน 1000 โดยตัวเลขคือเงินลงทุนเพิ่มหน่วยบาทที่แยกจากเงินฉุกเฉินแล้วครับ'
        budget = float(parts[1])
    # Short-lived reuse only when holdings, policy and requested budget match.
    basis = hashlib.sha256(json.dumps([portfolio, budget], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    from app.fundamentals import collect
    fundamentals = {} if settings.mock_mode else collect(settings, [h['symbol'] for h in portfolio['holdings']], now)
    evidence = {symbol: {k: value.get(k) for k in ('metrics', 'status', 'fetched_at')}
                for symbol, value in fundamentals.items()}
    basis = hashlib.sha256((basis+json.dumps(evidence, sort_keys=True)).encode()).hexdigest()
    key = 'decision:v3:'+now.astimezone(UTC).strftime('%Y-%m-%dT%H')+':'+basis[:20]
    saved = store.brief(key)
    if saved:
        return saved['message']
    # Headlines outside the recent window are not described as today's news.
    news = []
    for n in report.get('news', []):
        try:
            at = datetime.fromisoformat(n['published_at'].replace('Z', '+00:00'))
            if now-timedelta(days=14) <= at <= now:
                news.append(n)
        except (ValueError, TypeError):
            continue
    payload = review_payload(portfolio, news)
    payload.update(fundamentals=fundamentals, extra_investment_thb=budget,
                   extra_cash_is_separate_from_emergency=True if budget else None,
                   currency_conversion_available=False,
                   technical_context=[{'symbol': s['symbol'], 'indicators': s.get('indicators'),
                                       'stale': s.get('stale', True)} for s in report.get('stocks', [])])
    # No large reference URLs in the model prompt; attach the real sources below.
    compact = json.loads(json.dumps(payload))
    for company in compact['fundamentals'].values():
        for metric in company.get('metrics', {}).values():
            metric.pop('source_url', None)
    answer = None
    if not settings.mock_mode:
        from app.scheduled_briefs import interpret
        answer = interpret(settings, key, compact, now,
            'เขียนบันทึกช่วยตัดสินใจลงทุน: check_more=ข้อเสนอหลักตอนนี้และเหตุผล '
            'risks=หุ้นที่ควรทบทวนพร้อมหลักฐาน/ข้อมูลที่ยังขาด '
            'options=แผนมีเงื่อนไขว่าควรเพิ่ม รอ ลด หรือศึกษาอะไรต่อ '
            'เสนอแก้แผนเดิมได้หากมีเหตุผล แต่ระบุเป็นข้อเสนอที่ยังไม่ได้นำไปใช้ '
            'ห้ามใช้ราคาลงหรือกำไรของเจ้าของเป็นหลักฐานว่าหุ้นถูก/แพง '
            'ข้อมูล SEC เป็นงบรายปี ไม่ใช่งบไตรมาสล่าสุดหรือ TTM ระบุวันที่งบเมื่ออ้าง '
            'ห้ามฟันธงซื้อทันทีหรือคัดหุ้นใหม่จากความจำเมื่อไม่มีข้อมูลมูลค่าและงบปัจจุบัน '
            'ไม่มีงบลงทุนเพิ่มห้ามกำหนดจำนวนเงินหรือเปอร์เซ็นต์เงินสำรอง '
            'มีงบเพิ่มให้เสนอทางเลือกแบ่งจังหวะตามงบได้ ห้ามแปลงบาทเป็น USD หรือ %พอร์ตเพราะไม่มี FX '
            'อย่าเดาวันงบ เหตุผลราคาขยับ หรือผลตอบแทนจริงหลัง DCA '
            'หากข้อมูลไม่พอ ให้ข้อสรุปที่ลงมือทำได้คือรอหลักฐานใด พร้อมอธิบายว่าเหตุใด')
    message = 'แผนลงทุนสำหรับพอร์ตเราครับ\nจัดทำ '+thai_time(now.isoformat())
    if answer:
        message += '\n\n'+answer['check_more']+'\n\nหุ้นและประเด็นที่ควรทบทวน\n'+answer['risks']+'\n\nแผนที่เสนอ\n'+answer['options']
    else:
        message += ('\n\nรอบนี้ AI ยังไม่พร้อม ผมแสดงข้อมูลที่ตรวจได้ให้ก่อนครับ '
                    'ยังไม่มีข้อเสนอเปลี่ยนแผน DCA เดิม')
    available = sum(bool(f.get('metrics')) for f in fundamentals.values())
    message += f'\n\nงบรายปีที่อ่านได้ {available}/{len(portfolio["holdings"])} บริษัท · ข่าวที่คัดไว้ {len(news)} รายการ'
    if any(f.get('status') == 'not_configured' for f in fundamentals.values()):
        message += '\nการดึงงบ SEC ยังรอตั้งค่าอีเมลติดต่อ จึงยังไม่มีงบใหม่ประกอบคำแนะนำครับ'
    if not portfolio.get('live', {}).get('complete') or portfolio.get('live', {}).get('stale'):
        message += '\nราคาบางตัวยังไม่ครบหรือเก่า ควรอัปเดตก่อนตัดสินใจเรื่องจังหวะซื้อครับ'
    if budget is None:
        message += '\nถ้ามีงบเพิ่มแยกจากเงินฉุกเฉิน พิมพ์ แผนลงทุน 1000 (บาท) เพื่อให้ผมพิจารณางบนั้นครับ'
    else:
        message += f'\nงบลงทุนเพิ่มที่ใช้พิจารณารอบนี้ {budget:,.2f} บาท · ยังไม่บันทึกเป็นเงินสดหรือเปลี่ยน DCA'
    from app.web_news import news_reference
    seen = set()
    for item in news:
        if item['symbol'] in seen:
            continue
        seen.add(item['symbol'])
        if item.get('source_url'):
            message += '\n'+news_reference(item)
        if len(seen) >= 10:
            break
    for symbol, company in fundamentals.items():
        metric = next((m for m in company.get('metrics', {}).values() if m.get('source_url')), None)
        if metric:
            message += '\n'+news_reference(dict(symbol=symbol, title='งบรายปีสิ้นสุด '+metric['end'],
                source_name='SEC EDGAR', source_url=metric['source_url'], published_at=metric['filed']))
    store.save_brief(key, 'decision', now.timestamp(), message, payload, bool(answer))
    return message
