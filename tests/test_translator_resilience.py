"""标题翻译韧性:非思考模式 + 缺失项定向重试。"""

from __future__ import annotations

import pytest

from src.processors.llm_client import LLMResponse, LLMUsage
from src.processors.translator import translate_titles


class _SequenceClient:
    def __init__(self, responses: list[LLMResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict]] = []

    def chat(self, prompt: str, **kwargs) -> LLMResponse:
        self.calls.append((prompt, kwargs))
        return self.responses.pop(0)


def _ok(text: str) -> LLMResponse:
    return LLMResponse(text=text, usage=LLMUsage())


def _failed(reason: str = "empty") -> LLMResponse:
    return LLMResponse(text=None, usage=LLMUsage(), error=reason)


def test_translate_retries_only_missing_titles_in_non_thinking_mode() -> None:
    client = _SequenceClient([
        _ok("▦ 1: 美联储维持利率不变"),
        _ok("▦ 2: 美国国债收益率下降"),
    ])

    result = translate_titles(
        ["Fed holds rates", "Treasury yields fall"],
        client=client,
    )

    assert result == ["美联储维持利率不变", "美国国债收益率下降"]
    assert len(client.calls) == 2
    assert client.calls[0][1]["thinking"] is False
    assert client.calls[1][1]["thinking"] is False
    assert "Fed holds rates" not in client.calls[1][0]
    assert "Treasury yields fall" in client.calls[1][0]


def test_translate_returns_original_after_two_empty_attempts() -> None:
    client = _SequenceClient([_failed("first"), _failed("second")])

    result = translate_titles(["Fed holds rates"], client=client)

    assert result == ["Fed holds rates"]
    assert len(client.calls) == 2


def test_translate_does_not_retry_complete_batch() -> None:
    client = _SequenceClient([_ok("▦ 1: 美联储维持利率不变")])

    result = translate_titles(["Fed holds rates"], client=client)

    assert result == ["美联储维持利率不变"]
    assert len(client.calls) == 1


def test_multiline_source_is_one_numbered_payload_and_not_a_bare_headline():
    import json

    raw = 'China closes banks\nMore than 670 lenders closed last year.'
    full = '中国关闭银行。去年有超过 670 家贷款机构关闭。'
    client = _SequenceClient([_ok('▦ 1: ' + full)])
    assert translate_titles([raw], client=client) == [full]
    prompt, kwargs = client.calls[0]
    lines = prompt.splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0].removeprefix('▦ 1: ')) == raw
    assert '不能只翻第一行' in kwargs['task_extra']


def test_multiline_headline_only_translation_is_repaired_without_waiving_guards():
    import json

    raw = 'China closes banks\nMore than 670 lenders closed last year.'
    full = '中国关闭银行。去年有超过 670 家贷款机构关闭。'
    client = _SequenceClient([_ok('▦ 1: 中国关闭银行。'), _ok('▦ 1: ' + full)])
    diagnostics = {}
    assert translate_titles([raw], client=client, diagnostics=diagnostics) == [full]
    assert len(client.calls) == 2 and not diagnostics
    assert json.loads(client.calls[1][0].splitlines()[0].removeprefix('▦ 1: ')) == raw
    assert 'relative_calendar_period' in client.calls[1][0]


def test_json_transport_translation_is_decoded_before_strict_fact_checks():
    import json

    raw = 'China closes banks\nMore than 670 lenders closed last year.'
    translated = '中国关闭银行\n去年有超过 670 家贷款机构关闭。'
    client = _SequenceClient([_ok('▦ 1: ' + json.dumps(translated, ensure_ascii=False))])
    assert translate_titles([raw], client=client) == ['中国关闭银行。去年有超过 670 家贷款机构关闭。']


def test_transport_decode_cannot_hide_changed_quantity_or_time():
    import json

    raw = 'China closes banks\nMore than 670 lenders closed last year.'
    wrong = json.dumps('中国关闭银行\n今年有超过 760 家贷款机构关闭。', ensure_ascii=False)
    diagnostics = {}
    client = _SequenceClient([_ok('▦ 1: ' + wrong)] * 2)
    assert translate_titles([raw], client=client, diagnostics=diagnostics) == [raw]
    assert set(diagnostics[0]['errors']) >= {'relative_calendar_period', 'quantities_or_units'}


@pytest.mark.parametrize('first', ['中国关闭银行。', '“中国关闭银行。”', '银行数量变动：'])
def test_transport_join_keeps_existing_sentence_punctuation_and_real_quotes(first):
    import json

    from src.processors.translator import _parse_lines

    text = first + '\r\n“去年”有超过 670 家机构关闭。'
    assert _parse_lines('▦ 1: ' + json.dumps(text, ensure_ascii=False))[1] == first + '“去年”有超过 670 家机构关闭。'
    assert _parse_lines('▦ 1: “观点仍待证实”。')[1] == '“观点仍待证实”。'


def test_unquoted_or_malformed_literal_escapes_are_never_publication_authority():
    from src.processors.translation_guard import translation_errors

    assert 'serialized_translation_boundary' in translation_errors('China closes banks', r'中国关闭银行\n去年关闭机构')
    assert 'serialized_translation_boundary' in translation_errors('China closes banks', r'"中国关闭银行\n机构关闭')
