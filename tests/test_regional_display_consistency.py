import pytest

from src.processors.macro_topics import macro_topics
from src.renderer.render import _filter_display_metric_delta


@pytest.mark.parametrize('country', ['U.A.E.', 'United Arab Emirates', 'Qatar', 'Bahrain', 'Kuwait', 'Oman', 'Jordan', 'Lebanon', 'Syria', 'Yemen', 'Iraq', '阿联酋'])
def test_regional_security_coverage_groups_by_jurisdiction(country):
    assert macro_topics([f'{country} says attack was terrorism', 'Iran announces ceasefire']) == ['中东局势', '中东局势']


@pytest.mark.parametrize('country', ['Canada', 'Romania'])
def test_other_regional_security_does_not_get_merged(country):
    assert macro_topics([f'{country} says attack was terrorism', 'Iran announces ceasefire']) == ['地缘政治', '中东局势']


@pytest.mark.parametrize('current,prior,expected', [(31.174, 28.086, '+3.08'), (1.004, 1.006, '-0.01'), (1.001, 1.004, '—'), (None, 1, '—'), (float('nan'), 1, '—')])
def test_change_equals_displayed_observation_difference(current, prior, expected):
    assert _filter_display_metric_delta(current, prior) == expected
