"""单元测试:情绪指标采集的数值清洗。"""

from __future__ import annotations

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


def _cnn_payload(score: float, historical: list, previous_close: float | None = None) -> dict:
    fg: dict = {"score": score, "rating": "neutral"}
    if previous_close is not None:
        fg["previous_close"] = previous_close
    return {
        "fear_and_greed": fg,
        "fear_and_greed_historical": {"data": historical},
    }


# CNN historical x 字段为秒级时间戳(10 位)
_DAY_S = 86_400
_BASE_S = 1_748_822_400  # May 22 2026 00:00 UTC (秒)


def test_cnn_fg_prior_is_second_most_recent_entry(monkeypatch) -> None:
    """historical 按 x 倒排后取第 2 条作为 prior,正确区分当前快照与前一交易日。"""
    historical = [
        {"x": _BASE_S - 2 * _DAY_S, "y": 40.0},  # 3 天前
        {"x": _BASE_S - _DAY_S,     "y": 48.0},   # 昨日 ← 期望的 prior
        {"x": _BASE_S,               "y": 52.0},   # 最新快照(与 score 相同)
    ]
    payload = _cnn_payload(score=52.0, historical=historical)
    monkeypatch.setattr(
        sentiment.requests, "get", lambda *_a, **_kw: _FakeCNNResp(payload),
    )
    m = sentiment._fetch_cnn_fear_greed()
    assert m.current == pytest.approx(52.0)
    assert m.prior == pytest.approx(48.0)   # 前一日,不是最新快照
    assert m.prior != m.current


def test_cnn_fg_prior_works_with_seconds_unit(monkeypatch) -> None:
    """x 为秒级时间戳时,排序逻辑仍能取到正确的前一日值(复现 bug 场景)。"""
    # 模拟开盘前:score == 最新历史条目 y,原始 min(abs(x - target_ms)) 实现会选同一条
    historical = [
        {"x": _BASE_S - _DAY_S, "y": 58.57},  # 前一日
        {"x": _BASE_S,           "y": 60.83},  # 最新快照 == score
    ]
    payload = _cnn_payload(score=60.83, historical=historical)
    monkeypatch.setattr(
        sentiment.requests, "get", lambda *_a, **_kw: _FakeCNNResp(payload),
    )
    m = sentiment._fetch_cnn_fear_greed()
    assert m.current == pytest.approx(60.83)
    assert m.prior == pytest.approx(58.57)   # 应为前一日,不是 60.83


def test_cnn_fg_uses_previous_close_over_historical(monkeypatch) -> None:
    """fg.previous_close 存在时直接用作 prior,不再解析 historical。"""
    historical = [
        {"x": _BASE_S - _DAY_S, "y": 48.0},
        {"x": _BASE_S,           "y": 52.0},
    ]
    payload = _cnn_payload(score=52.0, historical=historical, previous_close=49.5)
    monkeypatch.setattr(
        sentiment.requests, "get", lambda *_a, **_kw: _FakeCNNResp(payload),
    )
    m = sentiment._fetch_cnn_fear_greed()
    assert m.current == pytest.approx(52.0)
    assert m.prior == pytest.approx(49.5)   # previous_close 优先,不是 historical[1]


def test_cnn_fg_prior_skips_intraday_entries(monkeypatch) -> None:
    """historical 含日内多条记录时,跨日找前一交易日而非取 sorted_hist[1]。"""
    _HOUR_S = 3600
    historical = [
        {"x": _BASE_S - _DAY_S,        "y": 48.0},   # 前一日
        {"x": _BASE_S,                  "y": 60.0},   # 今日 00:00
        {"x": _BASE_S + _HOUR_S,        "y": 60.5},   # 今日 +1h
        {"x": _BASE_S + _HOUR_S * 2,   "y": 61.0},   # 今日 +2h (最新)
    ]
    payload = _cnn_payload(score=61.0, historical=historical)
    monkeypatch.setattr(
        sentiment.requests, "get", lambda *_a, **_kw: _FakeCNNResp(payload),
    )
    m = sentiment._fetch_cnn_fear_greed()
    assert m.current == pytest.approx(61.0)
    assert m.prior == pytest.approx(48.0)   # 前一日,不是同日较早条目


def test_cnn_fg_prior_handles_millisecond_timestamps(monkeypatch) -> None:
    """x 为毫秒级时间戳时,日历日判断仍正确。"""
    _DAY_MS = _DAY_S * 1000
    _BASE_MS = _BASE_S * 1000
    historical = [
        {"x": _BASE_MS - _DAY_MS, "y": 55.0},   # 前一日(毫秒)
        {"x": _BASE_MS,            "y": 62.0},   # 今日(毫秒)
    ]
    payload = _cnn_payload(score=62.0, historical=historical)
    monkeypatch.setattr(
        sentiment.requests, "get", lambda *_a, **_kw: _FakeCNNResp(payload),
    )
    m = sentiment._fetch_cnn_fear_greed()
    assert m.current == pytest.approx(62.0)
    assert m.prior == pytest.approx(55.0)


def test_fetch_yfinance_close_drops_nonfinite_values(monkeypatch) -> None:
    monkeypatch.setattr(
        sentiment.yf,
        "Ticker",
        lambda _ticker: _FakeTicker([100.0, float("nan"), None, float("inf"), 101.5]),
    )

    assert sentiment._fetch_yfinance_close("^VIX", period="3mo") == [100.0, 101.5]
