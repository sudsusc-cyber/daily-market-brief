"""单元测试:持仓信号判断逻辑(纯函数,不打外部 API)"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pandas as pd
import pytest

from src.collectors import stocks
from src.collectors.stocks import _judge_signal
from src.config import BUY_STRATEGIES, HOLDINGS, buy_strategy


@pytest.fixture(autouse=True)
def offline_stock_clock(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 4, 0, tzinfo=UTC).astimezone(tz or UTC)
    monkeypatch.setattr(stocks, "datetime", Clock)
    monkeypatch.setattr(stocks.requests, "get", lambda *a, **k: pytest.fail("unexpected live market request"))


def _history(values):
    return pd.DataFrame({"Close": list(values)},
                        index=pd.date_range(end="2026-08-31", periods=len(values), freq="W-MON"))


def _ticker(price):
    return SimpleNamespace(fast_info=SimpleNamespace(last_price=price),
                           history_metadata={"regularMarketTime": datetime(2026, 9, 3, 20, tzinfo=UTC).timestamp()})


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


def test_nonfinite_live_price_falls_back_to_weekly_close(monkeypatch) -> None:
    closes = pd.Series([100.0 + i / 10 for i in range(220)])
    history = _history(closes)
    fake_ticker = _ticker(float("nan"))
    monkeypatch.setattr(stocks.yf, "Ticker", lambda _symbol: fake_ticker)
    monkeypatch.setattr(stocks, "_yf_history", lambda _ticker: history)

    signal = stocks.fetch_one(HOLDINGS[0])

    assert signal.error is None
    assert signal.last_close == closes.iloc[-1]


def test_each_holding_has_exactly_one_strategy():
    configured = [ticker for strategy in BUY_STRATEGIES for ticker in strategy.tickers]
    assert len(configured) == len(set(configured)) == len(HOLDINGS)
    assert set(configured) == {holding.ticker for holding in HOLDINGS}
    assert buy_strategy("GOOGL") == buy_strategy("GOOG")
    with pytest.raises(ValueError, match="未配置"):
        buy_strategy("UNKNOWN")


@pytest.mark.parametrize("ticker", ["MSFT", "AAPL", "GOOG", "BRK.B", "QQQM", "0700.HK", "9992.HK"])
@pytest.mark.parametrize("price,expected", [(141, "NONE"), (140, "DCA"), (130, "DCA"), (120, "LUMP_SUM"), (110, "LUMP_SUM")])
def test_weekly_groups_use_two_layers_every_day(ticker, price, expected):
    for _ in range(2):
        assert _judge_signal(price, 140, 120, ticker=ticker) == expected


@pytest.mark.parametrize("ticker", ["COST", "MA", "MCO", "KO", "LIN", "AXP"])
@pytest.mark.parametrize("price,expected", [(150, "NONE"), (140, "NONE"), (130, "NONE"), (120, "LUMP_SUM"), (110, "LUMP_SUM")])
def test_deep_group_never_uses_120w_dca(ticker, price, expected):
    assert _judge_signal(price, 140, 120, ticker=ticker) == expected


@pytest.mark.parametrize("ticker", ["NVDA", "TSM"])
@pytest.mark.parametrize("price,expected", [(201, "NONE"), (200, "DCA"), (170, "DCA"), (140, "LUMP_SUM"), (100, "LUMP_SUM")])
def test_growth_group_uses_250d_and_120w(ticker, price, expected):
    assert _judge_signal(price, 140, 90, ticker=ticker, sma_250d=200) == expected


def test_lump_layer_remains_priority_when_averages_cross():
    assert _judge_signal(130, 140, 90, ticker="NVDA", sma_250d=120) == "LUMP_SUM"


@pytest.mark.parametrize("daily", [None, float("nan"), float("inf"), 0, -1])
def test_growth_missing_daily_never_falls_back_to_wrong_weekly_strategy(daily):
    with pytest.raises(ValueError):
        _judge_signal(130, 140, 90, ticker="TSM", sma_250d=daily)


def test_growth_average_uses_exact_last_250_daily_closes():
    daily = [1000.0] * 100 + [float(i) for i in range(101, 351)]
    signal = stocks._build_signal(
        next(h for h in HOLDINGS if h.ticker == "NVDA"),
        closes=[100.0] * 150,
        daily_closes=daily,
        live_price=200,
        data_source="fixture",
    )
    assert signal.sma_250d == pytest.approx(225.5)
    assert signal.sma_120 == 100
    assert signal.sma_200 is None  # 该组不需要200周历史
    assert signal.signal == "DCA"
    assert [line["value"] for line in signal.buy_lines] == [225.5, 100]


def test_growth_unused_200w_missing_bar_does_not_block_valid_strategy():
    signal = stocks._build_signal(
        next(h for h in HOLDINGS if h.ticker == "NVDA"),
        closes=[float("nan")] * 80 + [100.0] * 120,
        daily_closes=[150.0] * 250,
        live_price=130,
        data_source="fixture",
    )
    assert signal.sma_200 is None
    assert signal.sma_120 == 100
    assert signal.sma_250d == 150
    assert signal.signal == "DCA"


@pytest.mark.parametrize("daily", [[100.0] * 249, [100.0] * 249 + [float("nan")]])
def test_growth_rejects_incomplete_daily_window(daily):
    with pytest.raises(ValueError):
        stocks._build_signal(
            next(h for h in HOLDINGS if h.ticker == "TSM"),
            closes=[100.0] * 220, daily_closes=daily,
            live_price=90, data_source="fixture",
        )


def test_daily_history_request_is_daily_and_unadjusted():
    calls = []

    def history(**kwargs):
        calls.append(kwargs)
        return pd.DataFrame({"Close": [150.0] * 300})

    stocks._yf_daily_history(SimpleNamespace(ticker="NVDA", history=history))
    assert calls == [{"period": "2y", "interval": "1d", "auto_adjust": False, "timeout": 20}]


def test_growth_daily_primary_failure_uses_daily_backup(monkeypatch):
    ticker = _ticker(130)
    monkeypatch.setattr(stocks.yf, "Ticker", lambda _: ticker)
    monkeypatch.setattr(stocks, "_yf_history", lambda _: _history([100.0] * 220))
    monkeypatch.setattr(stocks, "_yf_daily_history", lambda _: (_ for _ in ()).throw(RuntimeError("daily failed")))
    monkeypatch.setattr(stocks, "_yahoo_chart_daily", lambda _: ([150.0] * 300, 130))
    signal = stocks.fetch_one(next(h for h in HOLDINGS if h.ticker == "NVDA"))
    assert signal.error is None
    assert signal.sma_250d == 150
    assert signal.signal == "DCA"
    assert signal.data_source == "yfinance+daily:yahoo_chart"


def test_growth_daily_both_fail_returns_error_not_weekly_signal(monkeypatch):
    def fail(_):
        raise RuntimeError("unavailable")

    monkeypatch.setattr(stocks, "_yf_daily_history", fail)
    monkeypatch.setattr(stocks, "_yahoo_chart_daily", fail)
    signal = stocks.fetch_one(next(h for h in HOLDINGS if h.ticker == "TSM"))
    assert signal.error and "250 日线" in signal.error
    assert signal.signal == "NONE"


def test_single_line_group_does_not_fetch_daily(monkeypatch):
    ticker = _ticker(130)
    monkeypatch.setattr(stocks.yf, "Ticker", lambda _: ticker)
    monkeypatch.setattr(stocks, "_yf_history", lambda _: _history([100.0] * 220))
    monkeypatch.setattr(stocks, "_yf_daily_history", lambda _: pytest.fail("unexpected daily fetch"))
    signal = stocks.fetch_one(next(h for h in HOLDINGS if h.ticker == "COST"))
    assert signal.error is None
    assert signal.signal == "NONE"
    assert [line["label"] for line in signal.buy_lines] == ["200 周"]


@pytest.mark.parametrize("ticker", ["COST", "MA", "MCO", "KO", "LIN", "AXP"])
def test_observation_120w_is_displayed_but_never_adds_dca(ticker):
    holding = next(h for h in HOLDINGS if h.ticker == ticker)
    signal = stocks._build_signal(
        holding, closes=[100.0] * 80 + [140.0] * 120,
        live_price=130.0, data_source="fixture",
    )
    assert signal.signal == "NONE"  # 低于120周但高于200周，仍无 DCA。
    assert [line["value"] for line in signal.reference_lines] == [140.0, 124.0]
    assert signal.strategy.reference_line_labels == ("120 周", "200 周")
    assert [line["label"] for line in signal.buy_lines] == ["200 周"]
    assert signal.strategy.line_labels == ("200 周",)
    assert signal.reference_lines[0]["delta"] == pytest.approx(130 / 140 - 1)
