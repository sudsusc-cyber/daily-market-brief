"""标题翻译韧性:非思考模式 + 缺失项定向重试。"""

from __future__ import annotations

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
