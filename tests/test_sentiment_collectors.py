"""单元测试:情绪指标采集的数值清洗。"""

from __future__ import annotations

from datetime import UTC
from datetime import datetime as _dt

import pytest

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


class _FakeCNNResp:
    def __init__(self, data: dict) -> None:
        self._data = data
        self.status_code = 200

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._data


def _cnn_payload(score: float, previous_close: float | None, historical: list) -> dict:
    fg: dict = {"score": score, "rating": "neutral"}
    if previous_close is not None:
        fg["previous_close"] = previous_close
    return {"fear_and_greed": fg, "fear_and_greed_historical": {"data": historical}}


def test_cnn_fg_uses_previous_close_when_available(monkeypatch) -> None:
    """API 提供 previous_close 时直接使用,不走 historical 查找。"""
    day_ms = 86_400_000
    now_ms = 1_748_822_400_000  # 固定时间戳,避免依赖系统时间
    historical = [
        {"x": now_ms - 2 * day_ms, "y": 40.0},
        {"x": now_ms - day_ms, "y": 50.0},  # 昨日快照 = score,模拟开盘前
    ]
    payload = _cnn_payload(score=50.0, previous_close=45.0, historical=historical)
    monkeypatch.setattr(
        sentiment.requests, "get", lambda *_a, **_kw: _FakeCNNResp(payload),
    )
    m = sentiment._fetch_cnn_fear_greed()
    assert m.current == pytest.approx(50.0)
    # previous_close 应取代 historical 最新条目(50.0),给出正确的前日值
    assert m.prior == pytest.approx(45.0)
    assert m.prior != m.current


def test_cnn_fg_historical_fallback_skips_today(monkeypatch) -> None:
    """无 previous_close 时,fallback 应跳过今日条目,取今日零点之前最近的值。"""
    day_ms = 86_400_000
    today_midnight_ms = int(
        _dt.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000
    )

    historical = [
        {"x": today_midnight_ms - 2 * day_ms, "y": 40.0},   # 2 天前
        {"x": today_midnight_ms - day_ms,      "y": 48.0},   # 昨日(今日零点前)
        {"x": today_midnight_ms + 3_600_000,   "y": 52.0},   # 今日快照(与 score 相同)
    ]
    payload = _cnn_payload(score=52.0, previous_close=None, historical=historical)
    monkeypatch.setattr(
        sentiment.requests, "get", lambda *_a, **_kw: _FakeCNNResp(payload),
    )
    m = sentiment._fetch_cnn_fear_greed()
    assert m.current == pytest.approx(52.0)
    # fallback 应取"今日零点之前最近"的条目,即昨日(48.0),而非今日快照(52.0)
    assert m.prior == pytest.approx(48.0)
    assert m.prior != m.current


def test_fetch_yfinance_close_drops_nonfinite_values(monkeypatch) -> None:
    monkeypatch.setattr(
        sentiment.yf,
        "Ticker",
        lambda _ticker: _FakeTicker([100.0, float("nan"), None, float("inf"), 101.5]),
    )

    assert sentiment._fetch_yfinance_close("^VIX", period="3mo") == [100.0, 101.5]
