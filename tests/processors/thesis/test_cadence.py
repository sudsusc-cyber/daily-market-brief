"""test_cadence.py — cadence 映射逻辑单测"""

import pytest

from src.processors.thesis.cadence import CADENCE_DAYS, resolve_cadence, stale_days


@pytest.mark.parametrize("theme,horizon,expected", [
    # 关键字命中（hyphen-separated）
    ("ai-capex-cycle", "quarterly", "quarterly"),
    ("ai-demand-shift", "quarterly", "quarterly"),
    ("inference-cost-decline", "multi_year", "quarterly"),
    ("compute-constraint", "structural", "quarterly"),
    ("pricing-power", "quarterly", "quarterly"),
    ("margin-expansion", "quarterly", "quarterly"),
    ("capital-allocation-buyback", "quarterly", "slow"),
    ("management-succession", "quarterly", "slow"),
    ("regulatory-antitrust", "quarterly", "fast"),
    ("export-control", "quarterly", "fast"),
    ("brand-strength", "quarterly", "structural"),
    ("moat-widening", "quarterly", "structural"),
    ("industry-structure-change", "quarterly", "structural"),
    ("enterprise-revenue-growth", "quarterly", "quarterly"),
    ("inference", "quarterly", "quarterly"),
    # 关键字 miss → horizon 退化
    ("some-unknown-theme", "structural", "structural"),
    ("some-unknown-theme", "multi_year", "slow"),
    ("some-unknown-theme", "quarterly", "quarterly"),
])
def test_resolve_cadence(theme, horizon, expected):
    assert resolve_cadence(theme, horizon) == expected


def test_keyword_match_case_insensitive():
    assert resolve_cadence("AI-CAPEX", "quarterly") == "quarterly"
    assert resolve_cadence("Export-Control", "quarterly") == "fast"


@pytest.mark.parametrize("cadence,expected_days", [
    ("fast", 90),
    ("quarterly", 180),
    ("slow", 360),
    ("structural", 720),
])
def test_stale_days(cadence, expected_days):
    assert stale_days(cadence) == expected_days
    assert CADENCE_DAYS[cadence] == expected_days
