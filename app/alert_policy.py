"""Per-portfolio stock thresholds; shared stocks are fetched only once."""
from dataclasses import replace

from app.config import positive

FIELDS = {'price_drop_pct': 'เตือนราคาลง (%)', 'price_rise_pct': 'เตือนราคาขึ้น (%)',
          'rsi_low': 'RSI ต่ำกว่า', 'rsi_high': 'RSI สูงกว่า'}


def validate(value):
    if not isinstance(value, dict) or set(value) - (set(FIELDS) | {'alert_escalation'}):
        raise ValueError('รูปแบบเกณฑ์แจ้งเตือนไม่ถูกต้อง')
    for key in FIELDS:
        if key not in value:
            continue
        number = positive(value[key], FIELDS[key])
        if number > (99 if key.startswith('rsi_') else 500):
            raise ValueError('เกณฑ์แจ้งเตือนอยู่นอกช่วงที่รองรับ')
    if 'rsi_low' in value and 'rsi_high' in value and value['rsi_low'] >= value['rsi_high']:
        raise ValueError('RSI ต่ำต้องน้อยกว่า RSI สูง')
    if 'alert_escalation' in value and not isinstance(value['alert_escalation'], bool):
        raise ValueError('การเตือนระดับที่สูงขึ้นต้องเป็นเปิดหรือปิด')


def policies(settings, symbol, *, selected=False):
    if settings.mock_mode:
        return []
    from app.portfolio_catalog import PortfolioCatalog
    catalog = PortfolioCatalog(settings)
    value = catalog.read()
    portfolios = [catalog.selected(value)] if selected else value['portfolios']
    result = []
    for p in portfolios:
        stock = next((s for s in p['stocks'] if s['symbol'] == symbol), None)
        if stock:
            custom = stock.get('alert_settings', {})
            effective = {key: float(custom.get(key, getattr(settings, key))) for key in FIELDS}
            effective['alert_escalation'] = custom.get('alert_escalation', settings.alert_escalation)
            if effective['rsi_low'] >= effective['rsi_high']:
                raise ValueError('เกณฑ์ RSI รายหุ้นขัดกับค่าเริ่มต้น ตรวจการตั้งค่าแจ้งเตือน')
            result.append(dict(id=p['id'], name=p['name'], values=effective))
    return result


def effective_settings(settings, symbol, *, selected=False):
    rows = policies(settings, symbol, selected=selected)
    if not rows:
        return settings
    values = {key: (max if key == 'rsi_low' else min)(r['values'][key] for r in rows) for key in FIELDS}
    values['alert_escalation'] = any(r['values']['alert_escalation'] for r in rows)
    return replace(settings, **values)
