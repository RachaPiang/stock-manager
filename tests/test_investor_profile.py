import pytest

from app.investor_profile import analysis_context, profile_context, readiness_note


def portfolio(profile):
    return {'investor_profile': profile}


def test_allowlisted_context_keeps_only_decision_relevant_profile():
    result = analysis_context(portfolio({
        'age_years': 17, 'life_stage': 'student', 'emergency_fund': 'separate reserve',
        'company_preferences': ['moat'], 'address': 'must never leave this machine'}))
    assert result['age_years'] == 17
    assert result['company_preferences'] == ['moat']
    assert 'address' not in result


def test_invalid_profile_is_rejected_instead_of_becoming_model_prompt():
    with pytest.raises(ValueError):
        profile_context(portfolio({'age_years': '17'}))
    with pytest.raises(ValueError):
        profile_context(portfolio({'company_preferences': 'anything'}))


def test_reserve_is_never_treated_as_extra_investment_cash():
    assert 'ไม่เสนอ' in readiness_note(portfolio({'emergency_fund': 'มีเงินสำรองแยกต่างหาก'}))
