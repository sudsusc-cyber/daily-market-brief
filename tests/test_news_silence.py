"""generate_silence_note 的单元测试,不调 DeepSeek API。"""

from __future__ import annotations

from dataclasses import dataclass
from unittest import mock

from src.processors.news_summarizer import generate_silence_note


@dataclass
class _FakeChat:
    text: str
    error: str | None = None


def test_silence_clean_output() -> None:
    with mock.patch("src.processors.news_summarizer.LLMClient") as mock_client:
        mock_client.return_value.chat.return_value = _FakeChat(
            text="市声远去,只有时间在走。"
        )
        result = generate_silence_note(mock_client.return_value)
        assert result == "市声远去,只有时间在走。"


def test_silence_strips_quotes() -> None:
    with mock.patch("src.processors.news_summarizer.LLMClient") as mock_client:
        mock_client.return_value.chat.return_value = _FakeChat(
            text='"商海无波,舟自徐行。"'
        )
        result = generate_silence_note(mock_client.return_value)
        assert result == "商海无波,舟自徐行。"


def test_silence_strips_markdown_backticks() -> None:
    with mock.patch("src.processors.news_summarizer.LLMClient") as mock_client:
        mock_client.return_value.chat.return_value = _FakeChat(
            text="```\n商海无波,舟自徐行\n```"
        )
        result = generate_silence_note(mock_client.return_value)
        assert result == "商海无波,舟自徐行"


def test_silence_truncates_over_50_chars() -> None:
    with mock.patch("src.processors.news_summarizer.LLMClient") as mock_client:
        long_text = "今日商海无风无浪,旌旗未动营垒安然,诸公司皆沉默如山,市场静静等待下一个信号或风暴。"
        mock_client.return_value.chat.return_value = _FakeChat(text=long_text)
        result = generate_silence_note(mock_client.return_value)
        assert result is not None
        assert len(result) <= 52  # 截断后最多 50 字 + "。"
        assert result.endswith("。")


def test_silence_empty_response_returns_none() -> None:
    with mock.patch("src.processors.news_summarizer.LLMClient") as mock_client:
        mock_client.return_value.chat.return_value = _FakeChat(text="", error="timeout")
        result = generate_silence_note(mock_client.return_value)
        assert result is None
