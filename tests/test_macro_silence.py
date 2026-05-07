"""generate_silence_note 的单元测试(宏观版),不调 DeepSeek API。"""

from __future__ import annotations

from dataclasses import dataclass
from unittest import mock

from src.processors.macro_filter import generate_silence_note


@dataclass
class _FakeChat:
    text: str
    error: str | None = None


def test_silence_clean_output() -> None:
    with mock.patch("src.processors.macro_filter.LLMClient") as MockClient:
        MockClient.return_value.chat.return_value = _FakeChat(
            text="风未起,江湖自平。"
        )
        result = generate_silence_note(MockClient.return_value)
        assert result == "风未起,江湖自平。"


def test_silence_strips_quotes() -> None:
    with mock.patch("src.processors.macro_filter.LLMClient") as MockClient:
        MockClient.return_value.chat.return_value = _FakeChat(
            text='"四海无波,日升月落而已。"'
        )
        result = generate_silence_note(MockClient.return_value)
        assert result == "四海无波,日升月落而已。"


def test_silence_truncates_over_50_chars() -> None:
    with mock.patch("src.processors.macro_filter.LLMClient") as MockClient:
        long_text = "穹顶之下万物有序今日无惊雷,星图如昨寰宇安然,所有央行沉默所有冲突停歇所有数据都符合预期。"
        MockClient.return_value.chat.return_value = _FakeChat(text=long_text)
        result = generate_silence_note(MockClient.return_value)
        assert result is not None
        assert len(result) <= 52
        assert result.endswith("。")


def test_silence_empty_response_returns_none() -> None:
    with mock.patch("src.processors.macro_filter.LLMClient") as MockClient:
        MockClient.return_value.chat.return_value = _FakeChat(text="", error="timeout")
        result = generate_silence_note(MockClient.return_value)
        assert result is None
