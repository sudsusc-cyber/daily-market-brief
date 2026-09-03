"""单元测试:holdings_intro 的 _format_input 与 write_intro 容错路径。

不调用真实 LLM(用 mock client)。补 Round 2 审计指出的盲点。
"""
from __future__ import annotations

from unittest.mock import MagicMock

from src.collectors.stocks import StockSignal
from src.config import HOLDINGS
from src.processors.holdings_intro import _format_input, write_intro
from src.processors.llm_client import LLMResponse, LLMUsage


def _signal(idx: int, *, signal: str = "NONE", error: str | None = None,
            last: float | None = 100.0, s120: float | None = 95.0,
            s200: float | None = 90.0) -> StockSignal:
    return StockSignal(
        holding=HOLDINGS[idx],
        last_close=last, sma_120=s120, sma_200=s200,
        delta_120=(last - s120) / s120 if last and s120 else None,
        delta_200=(last - s200) / s200 if last and s200 else None,
        signal=signal,
        error=error,
    )


def _ok_resp(text: str) -> LLMResponse:
    return LLMResponse(text=text, usage=LLMUsage(), error=None)


def _bad_resp(err: str = "rate limit") -> LLMResponse:
    return LLMResponse(text=None, usage=LLMUsage(), error=err)


class TestFormatInput:
    def test_intro_uses_each_stocks_actual_strategy(self) -> None:
        from scripts.preview_email import _build_mock_signals

        out = _format_input(_build_mock_signals())
        cost = next(line for line in out.splitlines() if line.startswith("- COST"))
        nvda = next(line for line in out.splitlines() if line.startswith("- NVDA"))
        assert "200 周" in cost and "120 周" not in cost
        assert "250 日" in nvda and "120 周" in nvda and "200 周" not in nvda

    def test_format_includes_signal_summary(self) -> None:
        signals = [
            _signal(0, signal="LUMP_SUM"),
            _signal(1, signal="DCA"),
            _signal(2, signal="NONE"),
        ]
        out = _format_input(signals)
        assert "LUMP_SUM=1" in out and "DCA=1" in out and "无信号=1" in out

    def test_format_handles_error_rows(self) -> None:
        signals = [_signal(0, error="API rate limit reached")]
        out = _format_input(signals)
        assert "取数失败=1" in out
        assert "错误:API rate limit reached" in out

    def test_format_truncates_long_error(self) -> None:
        long_err = "X" * 200
        signals = [_signal(0, error=long_err)]
        out = _format_input(signals)
        # error 截到 40 字符
        assert "X" * 40 in out
        assert "X" * 41 not in out


class TestWriteIntro:
    def test_empty_signals_returns_none(self) -> None:
        client = MagicMock()
        assert write_intro([], client=client) is None
        client.chat.assert_not_called()

    def test_llm_failure_returns_none(self) -> None:
        client = MagicMock()
        client.chat.return_value = _bad_resp("timeout")
        out = write_intro([_signal(0)], client=client)
        assert out is None

    def test_llm_empty_text_returns_none(self) -> None:
        client = MagicMock()
        client.chat.return_value = _ok_resp("")
        assert write_intro([_signal(0)], client=client) is None

    def test_llm_strips_quotes_and_markdown(self) -> None:
        client = MagicMock()
        client.chat.return_value = _ok_resp('"潮水退去,礁石毕现。"')
        out = write_intro([_signal(0)], client=client)
        assert out == "潮水退去,礁石毕现。"

    def test_llm_strips_chinese_quotes(self) -> None:
        client = MagicMock()
        client.chat.return_value = _ok_resp("「市场如海,耐心如礁。」")
        out = write_intro([_signal(0)], client=client)
        assert out == "市场如海,耐心如礁。"

    def test_llm_strips_code_fence(self) -> None:
        client = MagicMock()
        client.chat.return_value = _ok_resp("```\n潮水退去,礁石毕现。\n```")
        out = write_intro([_signal(0)], client=client)
        assert "```" not in out
        assert "潮水退去" in out

    def test_llm_truncates_overflow(self) -> None:
        """LLM 溢出输出 → 截到 200 字加句号。"""
        long_text = "潮" * 300
        client = MagicMock()
        client.chat.return_value = _ok_resp(long_text)
        out = write_intro([_signal(0)], client=client)
        assert len(out) <= 201, f"截断后长度 {len(out)} 超出 201"
        assert out.endswith("。")
