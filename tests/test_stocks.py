"""单元测试:持仓信号判断逻辑(纯函数,不打外部 API)"""

from __future__ import annotations

from src.collectors.stocks import _judge_signal


class TestJudgeSignal:
    def test_no_signal_when_above_both(self) -> None:
        # 现价高于 200w 与 120w → NONE
        assert _judge_signal(last_close=150.0, sma_120=140.0, sma_200=120.0) == "NONE"

    def test_dca_when_below_120w_only(self) -> None:
        # 跌破 120w 但仍高于 200w → DCA
        assert _judge_signal(last_close=130.0, sma_120=140.0, sma_200=120.0) == "DCA"

    def test_lump_sum_when_below_200w(self) -> None:
        # 跌破 200w 直接 LUMP_SUM(也必然在 120w 之下,但优先判定更深的折扣)
        assert _judge_signal(last_close=110.0, sma_120=140.0, sma_200=120.0) == "LUMP_SUM"

    def test_lump_sum_when_at_200w_exact(self) -> None:
        # 等于 200w 临界值,按"跌破或等于"也算
        assert _judge_signal(last_close=120.0, sma_120=140.0, sma_200=120.0) == "LUMP_SUM"

    def test_dca_when_at_120w_exact(self) -> None:
        # 等于 120w 临界值
        assert _judge_signal(last_close=140.0, sma_120=140.0, sma_200=120.0) == "DCA"
