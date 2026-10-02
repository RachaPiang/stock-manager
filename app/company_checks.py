"""Auditable financial observations. These are not automatic buy/sell decisions."""
from datetime import date


def checks(company, notes, now):
    quarter = company.get('latest_quarter')
    output = []
    if not quarter:
        return [dict(status='missing', text='ยังไม่มีงบไตรมาสเดียวที่เทียบกันได้ ต้องตรวจงบหรือเอกสารบริษัทเพิ่มเติม')]
    age = (now.date() - date.fromisoformat(quarter['end'])).days
    if age > 150:
        output.append(dict(status='stale', text=f'งบไตรมาสล่าสุดสิ้นสุด {quarter["end"]} ({age} วันก่อน) ตรวจงบใหม่ก่อนตัดสินใจ'))
    metrics, trends = quarter['metrics'], quarter['trends']
    focus = notes.get('focus', [])
    for name, label, groups in [
        ('revenue_yoy_pct', 'รายได้', {'การเติบโต', 'งบ / แนวโน้มรายได้'}),
        ('net_income_yoy_pct', 'กำไรสุทธิ', {'กำไรและกระแสเงินสด', 'การเติบโต'}),
        ('operating_cash_yoy_pct', 'กระแสเงินสดจากการดำเนินงาน', {'กำไรและกระแสเงินสด'}),
    ]:
        if focus and not set(focus) & groups:
            continue
        value = trends.get(name)
        if value is not None:
            output.append(dict(status='review' if value < 0 else 'observation',
                text=f'{label}ไตรมาสนี้ {value:+.1f}% เทียบไตรมาสเดียวกันปีก่อน'))
    margin = trends.get('net_margin_change_pp')
    if margin is not None and (not focus or set(focus) & {'กำไรและกระแสเงินสด', 'ความได้เปรียบคู่แข่ง'}):
        output.append(dict(status='review' if margin <= -2 else 'observation',
                           text=f'อัตรากำไรสุทธิเปลี่ยน {margin:+.1f} จุดเปอร์เซ็นต์จากปีก่อน'))
    fcf = metrics.get('free_cash_flow')
    if fcf and fcf['value'] < 0 and (not focus or 'กำไรและกระแสเงินสด' in focus):
        output.append(dict(status='review', text='กระแสเงินสดอิสระไตรมาสนี้ติดลบ ตรวจเงินลงทุนและความต่อเนื่องก่อนเพิ่มน้ำหนัก'))
    if not trends:
        output.append(dict(status='missing', text='ยังไม่มีฐานไตรมาสเดียวกันปีก่อนที่ตรงช่วงและสกุลเงิน'))
    return output


def compact_company(company):
    """Latest evidence plus small trend history, not twelve full financial reports."""
    return {**{k: v for k, v in company.items() if k != 'quarters'},
            'quarter_history': [dict(end=q['end'], trends=q['trends']) for q in company.get('quarters', [])[-4:]]}
