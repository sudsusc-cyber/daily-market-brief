"""Fresh prose and factual signal phrasing share one generation and one gate."""
import json
from datetime import date
from types import SimpleNamespace

import pytest

from src.processors.holdings_intro import _signal_errors, write_intro

PROSE = ('价格从不负责解释自己，它只是把选择摆在面前。'
         '【持仓近况】真正的功课在别处：辨认那些在无人注视时依然一寸寸积累的价值，'
         '然后让事先写下的规则，替临场的情绪做决定。')
OTHER_PROSE = ('树木的年轮从不催促季节，根系却始终在看不见的地方生长。'
               '【持仓近况】认真理解一门生意的价值，也需要把热闹留在窗外，'
               '给思考留下足够宽阔的余地，让耐心与判断一同生长。')


def signal(name, kind, error=None):
    return SimpleNamespace(holding=SimpleNamespace(name=name), signal=kind, error=error)


def rows():
    return [signal('甲企业', 'LUMP_SUM'), signal('乙企业', 'DCA'), signal('丙企业', 'NONE')]


class Client:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def chat(self, payload, **kwargs):
        self.calls.append((payload, kwargs))
        return SimpleNamespace(text=json.dumps(self.responses.pop(0), ensure_ascii=False))


@pytest.mark.parametrize('sentence', [
    '甲企业已合乎大额买入的尺度，乙企业为定投留出余地。',
    '甲企业落在大额买入线内，乙企业则与定投的节奏相合。',
    '乙企业仍在定投区间，甲企业契合大额买入的条件。',
    '甲企业依然满足既定的大额买入标准，乙企业尚留有定投空间。',
])
def test_model_writes_fresh_signal_sentence_and_places_it_inside_intact_prose(sentence):
    client = Client({'text': PROSE, 'signal_text': sentence})
    result = write_intro(rows(), client=client)
    assert result == PROSE.replace('【持仓近况】', sentence)
    assert len(client.calls) == 1
    assert '甲企业' in client.calls[0][0] and 'LUMP_SUM' in client.calls[0][0]
    assert '丙企业' not in sentence


def test_same_day_sends_use_model_composition_not_date_modulo():
    history = SimpleNamespace(today=date(2026, 10, 3), rows=[])
    a = '甲企业合乎大额买入的条件，乙企业留有定投的余地。'
    b = '甲企业已在大额买入线内，乙企业与定投的节拍相应。'
    first = write_intro(rows(), client=Client({'text': PROSE, 'signal_text': a}), history=history)
    second = write_intro(rows(), client=Client({'text': OTHER_PROSE, 'signal_text': b}), history=history)
    assert a in first and b in second and first != second


def test_changed_reflection_cannot_disguise_identical_published_signal_sentence():
    old_signal = '甲企业已合乎大额买入的尺度，乙企业为定投留出余地。'
    new_signal = '甲企业落在大额买入线内，乙企业则与定投的节奏相合。'
    history = SimpleNamespace(today=date(2026, 10, 3), rows=[{
        'date': '2026-10-03', 'section': 'holdings_intro',
        'text': PROSE.replace('【持仓近况】', old_signal),
    }])
    client = Client({'text': OTHER_PROSE, 'signal_text': old_signal},
                    {'text': OTHER_PROSE, 'signal_text': new_signal})
    result = write_intro(rows(), client=client, history=history)
    assert new_signal in result and len(client.calls) == 2
    assert 'recent_signal_repeat' in client.calls[1][0]
    assert len(history.rows) == 1  # Rendering a draft never publishes history.


@pytest.mark.parametrize('sentence', [
    '甲企业已合乎定投的尺度，乙企业为大额买入留出余地。',
    '甲企业未合乎大额买入的尺度，乙企业为定投留出余地。',
    '甲企业首次落在大额买入线内，乙企业则与定投的节奏相合。',
    '甲企业跌入大额买入线内，乙企业则与定投的节奏相合。',
    '甲企业落在大额买入线内，丙企业则与定投的节奏相合。',
    '甲企业落在大额买入线内，乙企业应立即定投。',
    '甲企业落在大额买入线内，乙企业则与定投的节奏相合，利润也在增长。',
    '甲企业落在大额买入线内。',
])
def test_factual_binding_rejects_state_reversal_new_trigger_advice_or_extra_claim(sentence):
    assert _signal_errors(sentence, rows(), [])


def test_pending_row_cannot_disappear_from_generated_signal_context():
    signals = [*rows(), signal('丁企业', 'NONE', 'timeout')]
    statement = '甲企业合乎大额买入的条件，乙企业留有定投的余地'
    assert _signal_errors(statement + '。', signals, []) == ['pending_signal_omitted']
    assert not _signal_errors(statement + '，另有数据待核。', signals, [])


@pytest.mark.parametrize('prose', [
    PROSE.replace('【持仓近况】', '并非【持仓近况】'),
    PROSE.replace('【持仓近况】', '【持仓近况】【持仓近况】'),
    PROSE.replace('【持仓近况】', ''),
])
def test_prose_cannot_negate_or_duplicate_factual_slot(prose):
    response = {'text': prose, 'signal_text': '甲企业合乎大额买入的条件，乙企业留有定投的余地。'}
    assert write_intro(rows(), client=Client(response, response)) is None


def test_no_signal_and_all_pending_remain_distinct():
    assert not _signal_errors('持仓尚未出现既定买入信号。', [signal('甲', 'NONE')], [])
    assert not _signal_errors('持仓数据尚待核实，暂不判断买入位置。', [signal('甲', 'NONE', 'timeout')], [])
    assert _signal_errors('持仓尚未出现既定买入信号。', [signal('甲', 'NONE', 'timeout')], [])


def test_missing_signal_field_is_repaired_instead_of_silently_appending_old_template():
    sentence = '甲企业合乎大额买入的条件，乙企业留有定投的余地。'
    client = Client({'text': PROSE.replace('【持仓近况】', '')},
                    {'text': PROSE, 'signal_text': sentence})
    assert sentence in write_intro(rows(), client=client)
    assert len(client.calls) == 2
    assert 'invalid_prose_fields' in client.calls[1][0]
    failed = Client({'text': PROSE}, {'text': PROSE})
    assert write_intro(rows(), client=failed) is None
    assert len(failed.calls) == 2


def test_adverb_only_changes_do_not_make_repeated_signal_wording_new():
    old = '甲企业已合乎大额买入的尺度，乙企业为定投留出余地。'
    new = '甲企业仍合乎大额买入的尺度，乙企业则为定投留出余地。'
    assert _signal_errors(new, rows(), [PROSE.replace('【持仓近况】', old)]) == ['recent_signal_repeat']


@pytest.mark.parametrize('sentence', ['持仓尚无买入信号。', '持仓未见既定买入信号。', '既定买入信号尚未出现。'])
def test_no_active_signal_can_be_paraphrased_without_weakening_unknown_handling(sentence):
    assert not _signal_errors(sentence, [signal('甲', 'NONE')], [])
    assert _signal_errors(sentence, [signal('甲', 'DCA')], [])
    assert _signal_errors(sentence, [signal('甲', 'NONE', 'timeout')], [])


@pytest.mark.parametrize('claim', [
    '甲企业并不满足大额买入条件。',
    '乙企业已经不适合定投。',
    '甲企业已经达到买入的门槛。',
    '乙企业合乎加仓标准。',
    '甲企业落在建仓区间。',
])
def test_slot_exterior_cannot_publish_a_second_unverified_strategy_claim(claim):
    from src.processors.holdings_intro import _intro_errors

    prose = claim + PROSE.replace('【持仓近况】', '')
    assert 'unsupported_observation_or_action' in _intro_errors(prose, '', [])
    response = {'text': claim + PROSE,
                'signal_text': '甲企业合乎大额买入的条件，乙企业留有定投的余地。'}
    assert write_intro(rows(), client=Client(response, response)) is None


def test_general_investment_reflection_remains_allowed_outside_verified_signal_sentence():
    from src.processors.holdings_intro import _intro_errors

    prose = ('每次买入之前，都值得问清自己究竟理解了什么。'
             '企业的价值常在不声不响中积累，判断也需要在反复求证中生长；'
             '把耐心留给经营，把谦逊留给未知，让时间照见那些扎实而安静的努力。')
    assert not _intro_errors(prose, '', [])
    statement = '甲企业合乎大额买入的条件，乙企业留有定投的余地。'
    assert write_intro(rows(), client=Client({'text': prose + '【持仓近况】',
                                              'signal_text': statement})) == prose + statement



def test_actual_preview_duplicate_signal_failure_repairs_to_integrated_non_report_prose():
    signals = [signal('腾讯控股', 'LUMP_SUM'), signal('泡泡玛特', 'DCA')]
    bad = {
        'text': '理解一家企业，往往不是靠某个季度的数字，而是靠时间慢慢显影它的选择。价格波动容易被误当成信息，其实多数时候只是噪声。当规则替代情绪做决定，耐心才有立足处。【持仓近况】腾讯控股已符合大额买入条件，泡泡玛特已符合定投条件。判断力不体现在预测上，而体现在区间到来时是否愿意照做。',
        'signal_text': '区间已至，纪律先行；腾讯控股候大额买入，泡泡玛特候定投。',
    }
    sentence = '腾讯控股与大额买入的尺度相契，泡泡玛特为定投留有余地。'
    client = Client(bad, {'text': PROSE, 'signal_text': sentence})
    result = write_intro(signals, client=client)
    assert result == PROSE.replace('【持仓近况】', sentence)
    assert result.count('腾讯控股') == result.count('泡泡玛特') == 1
    assert len(client.calls) == 2


@pytest.mark.parametrize('sentence', [
    '甲企业与大额买入的尺度相契，乙企业为定投留有余地。',
    '甲企业合乎既定的大额买入尺度，乙企业亦留有定投空间。',
    '乙企业与定投的节奏相应，甲企业在大额买入的尺度之内。',
])
def test_literary_current_state_can_vary_without_new_trigger_or_grammar_relaxation(sentence):
    assert not _signal_errors(sentence, rows(), [])
    assert _signal_errors(sentence.replace('甲企业', '丙企业'), rows(), [])


ACTUAL_TOLERANT_PROSE = (
    '企业的价值从不在一两个季度里显形，它藏在那些无人催促的日常经营里。'
    '判断一家公司是否值得托付，靠的不是聪明，而是愿意把时间拉长，让复利自己说话。'
    '【持仓近况】而耐心之所以稀缺，是因为它要求人在看不清时依然按既定尺度行事，不被一时的喧嚣带走。'
)


def test_actual_112_character_prose_and_neutral_current_position_do_not_force_fallback():
    statement = '腾讯控股现处大额买入区间，泡泡玛特则落在定投的尺度之内。'
    signals = [signal('腾讯控股', 'LUMP_SUM'), signal('泡泡玛特', 'DCA')]
    assert len(ACTUAL_TOLERANT_PROSE.replace('【持仓近况】', '')) == 112
    client = Client({'text': ACTUAL_TOLERANT_PROSE, 'signal_text': statement})
    result = write_intro(signals, client=client)
    assert result == ACTUAL_TOLERANT_PROSE.replace('【持仓近况】', statement)
    assert len(client.calls) == 1


@pytest.mark.parametrize('predicate', [
    '现处', '目前处于', '如今位于', '眼下在', '现在位处', '现今居于', '依然身在', '仍置身于',
])
def test_neutral_current_location_grammar_is_not_limited_to_one_verb(predicate):
    statement = f'甲企业{predicate}大额买入区间，乙企业仍在定投区间。'
    assert not _signal_errors(statement, rows(), [])
    assert _signal_errors(statement.replace('甲企业', '丙企业'), rows(), [])
    assert _signal_errors(statement.replace('大额买入', '定投'), rows(), [])


@pytest.mark.parametrize('predicate', ['现不处', '曾处于', '即将位于', '刚刚落入', '首次进入'])
def test_present_grammar_does_not_accept_negation_history_prediction_or_new_trigger(predicate):
    assert _signal_errors(f'甲企业{predicate}大额买入区间，乙企业仍在定投区间。', rows(), [])


def test_small_length_tolerance_has_a_hard_limit_and_never_truncates_prose():
    from src.processors.holdings_intro import _intro_errors

    prose = ACTUAL_TOLERANT_PROSE.replace('【持仓近况】', '')
    assert not _intro_errors(prose, '', [])
    oversized = prose + '时间会检验耐心，也会检验判断。'
    assert len(oversized) > 120
    assert 'format_or_length' in _intro_errors(oversized, '', [])


def test_length_repair_keeps_verified_signal_when_second_response_invents_reference_facts():
    statement = '甲企业现处大额买入区间，乙企业为定投留有余地。'
    too_long = ACTUAL_TOLERANT_PROSE + '时间会检验耐心，也会检验判断。'
    invented = '甲企业现价落在200周均线以内，可作大额买入；乙企业在120周均线以内，适合小额定投。'
    client = Client({'text': too_long, 'signal_text': statement},
                    {'text': PROSE, 'signal_text': invented})
    result = write_intro(rows(), client=client)
    assert result == PROSE.replace('【持仓近况】', statement)
    assert not any(term in result for term in ('200', '120', '均线', '现价'))
    assert 'signal_text已通过事实核验' in client.calls[1][0]
    assert statement in client.calls[1][0]
    assert len(client.calls) == 2


def test_signal_repair_cannot_destroy_previously_validated_literary_text():
    statement = '甲企业现处大额买入区间，乙企业为定投留有余地。'
    client = Client({'text': PROSE, 'signal_text': '甲企业首次跌入大额买入区间，乙企业仍在定投区间。'},
                    {'text': '甲企业并不满足大额买入条件。' + OTHER_PROSE, 'signal_text': statement})
    result = write_intro(rows(), client=client)
    assert result == PROSE.replace('【持仓近况】', statement)
    assert 'text已通过校验' in client.calls[1][0]
    assert '并不满足' not in result


def test_sentence_placeholder_cannot_leave_a_comma_after_its_inserted_full_stop():
    response = {'text': PROSE.replace('【持仓近况】', '【持仓近况】，'),
                'signal_text': '甲企业合乎大额买入的条件，乙企业留有定投的余地。'}
    assert write_intro(rows(), client=Client(response, response)) is None


def test_neutral_present_adverb_swaps_are_not_new_wording():
    old = '甲企业现处大额买入区间，乙企业仍在定投区间。'
    new = '甲企业目前处大额买入区间，乙企业依然在定投区间。'
    assert _signal_errors(new, rows(), [old]) == ['recent_signal_repeat']
