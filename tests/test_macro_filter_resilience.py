"""宏观视野加工韧性:非思考模式 + 空/无效输出重试。"""

from __future__ import annotations

from datetime import UTC, datetime

from src.collectors.macro_news import MacroFeedBundle, MacroNewsItem
from src.processors.llm_client import LLMResponse, LLMUsage
from src.processors.macro_filter import summarize


class _SequenceClient:
    def __init__(self, responses: list[LLMResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict]] = []

    def chat(self, prompt: str, **kwargs) -> LLMResponse:
        self.calls.append((prompt, kwargs))
        return self.responses.pop(0)


def _bundle() -> MacroFeedBundle:
    return MacroFeedBundle(
        source="WSJ",
        items=[MacroNewsItem(
            title="Fed holds rates",
            published_at=datetime(2026, 8, 4, tzinfo=UTC),
            url="https://example.com/fed",
            source="WSJ",
        )],
    )


def _response(text: str | None, error: str | None = None) -> LLMResponse:
    return LLMResponse(text=text, usage=LLMUsage(), error=error)


def test_macro_filter_retries_empty_output_without_thinking() -> None:
    client = _SequenceClient([
        _response(None, "ReasoningStarved"),
        _response("<p><strong>美联储。</strong>政策利率维持不变[1]</p>"),
    ])

    result = summarize([_bundle()], client=client)

    assert result is not None
    assert "美联储" in result.summary_html
    assert len(client.calls) == 2
    assert all(call[1]["thinking"] is False for call in client.calls)
    assert client.calls[0][1]["max_tokens"] == 2000


def test_macro_filter_retries_output_without_valid_footnotes() -> None:
    client = _SequenceClient([
        _response("<p><strong>美联储。</strong>政策利率维持不变</p>"),
        _response("<p><strong>美联储。</strong>政策利率维持不变[1]</p>"),
    ])

    result = summarize([_bundle()], client=client)

    assert result is not None
    assert len(client.calls) == 2
    assert "重试修正" in client.calls[1][1]["task_extra"]
