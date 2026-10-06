
import pytest

from src.processors.holdings_intro import (
    _compose_intro,
    _intro_boundaries,
    _signal_slot_errors,
    write_intro,
)
from tests.test_intro_composed_signals import PROSE, Client, rows


@pytest.mark.parametrize('separator', [';', '；', ' ; ', ' ； '])
@pytest.mark.parametrize('before', ['前文。', '前文；'])
def test_one_punctuation_boundary_without_changing_signal_or_prose(before, separator):
    frame = before + '【持仓近况】' + separator + '后文。'
    assert not _signal_slot_errors(frame)
    result = _compose_intro(frame, '甲企业合乎大额买入条件，乙企业有定投余地。')
    assert result == before + '甲企业合乎大额买入条件，乙企业有定投余地；后文。'
    assert '。；' not in result and ';' not in result


def test_received_preview_mixed_width_frame_is_normalized_before_generation_gate():
    text = PROSE.replace('【持仓近况】', '【持仓近况】;')
    statement = '甲企业合乎大额买入的尺度，乙企业留有定投的余地。'
    client = Client({'text': text, 'signal_text': statement})
    result = write_intro(rows(), client=client)
    assert result == PROSE.replace('【持仓近况】', statement[:-1] + '；')
    assert len(client.calls) == 1


@pytest.mark.parametrize('prefix', ['如果判断成立', '也许如此', 'If the condition holds', 'Perhaps'])
def test_width_normalization_does_not_bypass_conditional_or_uncertain_scope(prefix):
    assert _signal_slot_errors(prefix + ';【持仓近况】;后文。') == ['signal_slot_scope']


@pytest.mark.parametrize('punctuation', [',', ':', '?', '!', '，', '：', '？', '！'])
def test_slot_cannot_create_a_dangling_separator_or_question(punctuation):
    assert _signal_slot_errors('前文。【持仓近况】' + punctuation + '后文。')


def test_normalization_never_changes_reference_facts_to_gain_acceptance():
    client = Client(*[{'text': PROSE + ';', 'signal_text': '甲企业合乎定投条件，乙企业合乎大额买入条件。'}] * 2)
    assert write_intro(rows(), client=client) is None


@pytest.mark.parametrize('separator', [',', ' , ', '，', ' ， '])
def test_equivalent_signal_clause_separators_do_not_trigger_fallback(separator):
    statement = '甲企业合乎大额买入的尺度' + separator + '乙企业则有定投的余地。'
    frame = PROSE.replace('【持仓近况】', '【持仓近况】;').replace('，', ',')
    client = Client({'text': frame, 'signal_text': statement})
    result = write_intro(rows(), client=client)
    assert result and '甲企业合乎大额买入的尺度，乙企业则有定投的余地；' in result
    assert ',' not in result and ';' not in result
    assert len(client.calls) == 1


def test_comma_normalization_preserves_numeric_grouping():
    assert _intro_boundaries('1,234,567,然后继续;后文。') == '1,234,567，然后继续；后文。'


@pytest.mark.parametrize('separator', [',', '，'])
def test_comma_normalization_does_not_accept_changed_signal_strategy(separator):
    statement = '甲企业合乎定投的尺度' + separator + '乙企业有大额买入的余地。'
    client = Client(*[{'text': PROSE, 'signal_text': statement}] * 2)
    result = write_intro(rows(), client=client)
    assert result and '甲企业合乎大额买入的尺度' in result and '乙企业合乎定投的尺度' in result
    assert '甲企业合乎定投' not in result and '乙企业有大额买入' not in result
