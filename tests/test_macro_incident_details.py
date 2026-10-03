from types import SimpleNamespace

import pytest

from src.processors.news_selection import macro_candidate


@pytest.mark.parametrize('title', [
    'Oman Had Previously Banned the FlyDubai Attacker From Flying',
    'Attacker Had Been Banned From Flying for Radical Views',
    'Co-pilot Used Crash Ax in Attack, Says Government',
    'Police detail suspect background and biography',
    '副驾驶在袭击中使用了消防斧',
])
def test_individual_incident_details_need_wider_impact(title):
    assert not macro_candidate(SimpleNamespace(title=title, summary=''))
    assert macro_candidate(SimpleNamespace(title=title, summary='Government closes airspace and suspends international flights.'))


@pytest.mark.parametrize('title', [
    'Government says airline attack was terrorism',
    'Government introduces new aviation rules after attack',
    'Oil supply disrupted after port attack',
    'Airspace closed after attack',
])
def test_policy_market_and_cross_border_consequences_remain(title):
    assert macro_candidate(SimpleNamespace(title=title, summary=''))
