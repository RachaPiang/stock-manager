"""Decision-relevant investor context, kept locally with the portfolio."""
from __future__ import annotations


DEFAULT = {
    'life_stage': 'ยังไม่ได้ระบุ',
    'age_years': None,
    'experience': 'ยังไม่ได้ระบุ',
    'objective': 'ออมระยะยาว',
    'horizon': 'ยังไม่ได้ระบุ',
    'liquidity_need': 'ยังไม่ได้ระบุ',
    'emergency_fund': 'ยังไม่ได้ระบุ',
    'risk_tolerance': 'ยังไม่ได้ระบุ',
    'decision_style': 'เจ้าของพอร์ตตัดสินใจเอง',
    'company_preferences': [],
    'avoidances': [],
}


def _text(value, maximum=300):
    value = str(value).strip()
    if not value or len(value) > maximum:
        raise ValueError('ข้อมูล Investment Profile ไม่ถูกต้อง')
    return value


def profile_context(portfolio: dict) -> dict:
    """Return an allowlisted profile fit for an investment decision memo.

    Deliberately excludes identity, address, account balances, family details,
    and anything not useful for deciding how cautious a recommendation is.
    """
    raw = portfolio.get('investor_profile') or {}
    if not isinstance(raw, dict):
        raise ValueError('Investment Profile ต้องเป็นข้อมูลแบบ object')
    result = dict(DEFAULT)
    for key in ('life_stage', 'experience', 'objective', 'horizon', 'liquidity_need',
                'emergency_fund', 'risk_tolerance', 'decision_style'):
        if key in raw:
            result[key] = _text(raw[key])
    if raw.get('age_years') is not None:
        age = raw['age_years']
        if isinstance(age, bool) or not isinstance(age, int) or not 13 <= age <= 120:
            raise ValueError('อายุต้องเป็นจำนวนเต็มที่สมเหตุสมผล')
        result['age_years'] = age
    for key in ('company_preferences', 'avoidances'):
        if key in raw:
            values = raw[key]
            if not isinstance(values, list) or len(values) > 12:
                raise ValueError('รายการความชอบ Investment Profile ไม่ถูกต้อง')
            result[key] = [_text(value, 140) for value in values]
    return result


def analysis_context(portfolio: dict) -> dict:
    """The small private context that may be passed to the local Codex call."""
    profile = profile_context(portfolio)
    return {key: value for key, value in profile.items() if value not in (None, '', [])}


def readiness_note(portfolio: dict) -> str:
    """A concise guardrail used before asking for a precise cash recommendation."""
    profile = profile_context(portfolio)
    if 'แยก' in profile['emergency_fund']:
        return 'เงินสำรองถูกแยกจากการลงทุนแล้ว ระบบจะไม่เสนอให้ดึงเงินส่วนนั้นมาใช้'
    return 'ยังไม่ได้ยืนยันว่าเงินก้อนใดเป็นเงินลงทุนเพิ่ม จึงจะไม่ระบุเปอร์เซ็นต์เงินสดที่ควรใช้'
