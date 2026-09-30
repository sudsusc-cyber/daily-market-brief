"""Source-derived holdings introduction and audit input formatting."""
from __future__ import annotations

from unittest.mock import MagicMock

from src.collectors.stocks import StockSignal
from src.config import HOLDINGS
from src.processors.holdings_intro import _format_input, write_intro


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

    def test_model_failure_uses_template_fallback(self) -> None:
        client = MagicMock()
        client.chat.side_effect = RuntimeError("must not run")
        out = write_intro([_signal(0)], client=client)
        assert out is None
        assert client.chat.call_count == 2
