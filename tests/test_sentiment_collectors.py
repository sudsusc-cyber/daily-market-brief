"""单元测试:情绪指标采集的数值清洗。"""

from __future__ import annotations

from src.collectors import sentiment


def test_sentiment_metric_delta_treats_nonfinite_as_missing() -> None:
    metric = sentiment.SentimentMetric(
        name="VIX",
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

    assert sentiment._fetch_yfinance_close("^VIX", period="3mo") == [100.0, 101.5]
