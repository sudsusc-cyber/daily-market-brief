from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from src.processors.holdings_intro import daily_signal_sentence, fallback_intro


def signal(name, kind, error=None):
    return SimpleNamespace(holding=SimpleNamespace(name=name), signal=kind, error=error)


@pytest.mark.parametrize('variant', range(7))
def test_named_current_positions_preserve_strategy_without_inventing_daily_change(variant):
    rows = [signal('甲公司', 'LUMP_SUM'), signal('乙公司', 'DCA'), signal('丙公司', 'NONE')]
    text = daily_signal_sentence(rows, variant)
    assert '甲公司' in text and '大额' in text
    assert '乙公司' in text and '定投' in text
    assert '丙公司' not in text
    assert len(text) <= 50 and text.count('。') == 1
    assert not any(w in text for w in ('新触发', '上涨', '下跌', '建议', '只有部分'))


def test_unknown_is_never_treated_as_no_signal():
    rows = [signal('甲公司', 'NONE'), signal('乙公司', 'NONE', 'missing')]
    assert '待核' in daily_signal_sentence(rows)
    assert '都在' not in daily_signal_sentence(rows)
    assert '待核' in daily_signal_sentence([signal('甲公司', 'UNKNOWN')])


def test_many_active_positions_have_bounded_summary_and_no_omitted_warning():
    rows = [signal(f'Company {i}', 'DCA') for i in range(15)]
    text = daily_signal_sentence(rows)
    assert '15项持仓' in text and len(text) <= 50
    rows.append(signal('Missing', 'NONE', 'timeout'))
    assert '待核' in daily_signal_sentence(rows)


def test_fallback_preserves_prose_exactly_and_adds_same_verified_observation():
    when = datetime(2026, 10, 3, tzinfo=UTC)
    rows = [signal('甲公司', 'DCA')]
    plain = fallback_intro([], when)
    assert fallback_intro(rows, when) == plain + daily_signal_sentence(rows, when.date().toordinal())
    assert daily_signal_sentence([]) == ''
