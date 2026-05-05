"""单元测试:情绪指标采集的数值清洗。"""

from __future__ import annotations

import math

from src.collectors import sentiment


def test_sentiment_metric_delta_treats_nonfinite_as_missing() -> None:
    metric = sentiment.SentimentMetric(
        name="恒指 14 日 RSI",
        current=float("nan"),
        prior=48.0,
        rating=None,
    )

    assert metric.delta is None


class _FakeClose:
    def __init__(self, values: list[object]) -> None:
        self._values = values

    def tolist(self) -> list[object]:
        return self._values


class _FakeHistory:
    empty = False

    def __init__(self, closes: list[object]) -> None:
        self._closes = closes

    def __getitem__(self, key: str) -> _FakeClose:
        assert key == "Close"
        return _FakeClose(self._closes)


class _FakeTicker:
    def __init__(self, closes: list[object]) -> None:
        self._closes = closes

    def history(self, **_kwargs) -> _FakeHistory:
        return _FakeHistory(self._closes)


def test_fetch_yfinance_close_drops_nonfinite_values(monkeypatch) -> None:
    monkeypatch.setattr(
        sentiment.yf,
        "Ticker",
        lambda _ticker: _FakeTicker([100.0, float("nan"), None, float("inf"), 101.5]),
    )

    assert sentiment._fetch_yfinance_close("^HSI", period="3mo") == [100.0, 101.5]


def test_hsi_rsi_ignores_latest_nan(monkeypatch) -> None:
    closes = [
        100.0,
        101.0,
        99.0,
        102.0,
        100.0,
        105.0,
        103.0,
        106.0,
        104.0,
        107.0,
        109.0,
        108.0,
        110.0,
        111.0,
        112.0,
        111.0,
        float("nan"),
    ]
    monkeypatch.setattr(sentiment, "_fetch_yfinance_close", lambda *_args, **_kwargs: closes)

    metric = sentiment._fetch_hsi_rsi()

    assert metric.error is None
    assert metric.current is not None
    assert math.isfinite(metric.current)
    assert metric.prior is not None
    assert math.isfinite(metric.prior)
