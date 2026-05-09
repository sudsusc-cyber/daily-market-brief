"""单元测试:sentiment 模块韧性增强(ADR-0013)。

覆盖:
- _fetch_cboe_vix:CBOE 历史 JSON 解析
- _with_last_good:三层降级(成功 → 持久化 / 失败 → 沿用 / 失败 + 过期 → error 透传)
- fetch_all 集成:VIX 主路径 CBOE 失败 → yfinance 备 → last-good 兜底
- _filter_iso_date_md:模板渲染 stale_from 灰字标注
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from src.collectors import sentiment
from src.collectors.sentiment import (
    SentimentMetric,
    _fetch_cboe_vix,
    _with_last_good,
)
from src.renderer.render import _filter_iso_date_md
from src.utils.last_good import LastGoodCache

# ─── _fetch_cboe_vix ─────────────────────────────────────────────


class _FakeResp:
    def __init__(self, json_data: dict, status_code: int = 200) -> None:
        self._json = json_data
        self.status_code = status_code

    def json(self) -> dict:
        return self._json

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_cboe_vix_returns_last_two_closes(monkeypatch) -> None:
    payload = {
        "data": [
            {"date": "2026-05-01", "close": "16.5"},
            {"date": "2026-05-04", "close": "17.0"},
            {"date": "2026-05-05", "close": "17.5"},
            {"date": "2026-05-06", "close": "16.99"},
            {"date": "2026-05-07", "close": "17.83"},
        ],
    }
    monkeypatch.setattr(
        sentiment.requests, "get", lambda *_a, **_kw: _FakeResp(payload),
    )
    current, prior = _fetch_cboe_vix()
    assert current == pytest.approx(17.83)
    assert prior == pytest.approx(16.99)


def test_cboe_vix_skips_invalid_close_rows(monkeypatch) -> None:
    payload = {
        "data": [
            {"date": "2026-05-05", "close": "17.0"},
            {"date": "2026-05-06", "close": "not_a_number"},  # 跳过
            {"date": "2026-05-07", "close": "17.83"},
        ],
    }
    monkeypatch.setattr(
        sentiment.requests, "get", lambda *_a, **_kw: _FakeResp(payload),
    )
    current, prior = _fetch_cboe_vix()
    assert current == pytest.approx(17.83)
    assert prior == pytest.approx(17.0)


def test_cboe_vix_raises_on_empty(monkeypatch) -> None:
    monkeypatch.setattr(
        sentiment.requests, "get", lambda *_a, **_kw: _FakeResp({"data": []}),
    )
    with pytest.raises(RuntimeError, match="CBOE VIX 返回空数据"):
        _fetch_cboe_vix()


# ─── _with_last_good ─────────────────────────────────────────────


@pytest.fixture
def tmp_state(tmp_path: Path) -> Path:
    state = tmp_path / "state"
    state.mkdir()
    return state


def _make_ok(name: str, current: float, prior: float | None = None) -> SentimentMetric:
    return SentimentMetric(name=name, current=current, prior=prior, rating=None)


def _make_failed(name: str, msg: str = "boom") -> SentimentMetric:
    return SentimentMetric(
        name=name, current=None, prior=None, rating=None,
        error=f"RuntimeError: {msg}",
    )


def test_with_last_good_persists_on_success(tmp_state: Path) -> None:
    cache = LastGoodCache(tmp_state)
    today = date(2026, 5, 8)
    m = _with_last_good(
        lambda: _make_ok("VIX", 17.83, 16.99),
        cache=cache, cache_key="VIX", today=today,
    )
    assert m.error is None
    assert m.current == 17.83
    # 持久化验证:新实例能读到
    cached = LastGoodCache(tmp_state).get("sentiment.VIX")
    assert cached is not None
    assert cached[0]["current"] == 17.83
    assert cached[1] == "2026-05-08"


def test_with_last_good_falls_back_on_failure(tmp_state: Path) -> None:
    cache = LastGoodCache(tmp_state)
    # 先存一个成功值
    cache.put("sentiment.VIX", {"current": 17.5, "prior": 16.9, "rating": None, "unit": ""},
              today=date(2026, 5, 7))
    # 第二天失败:应沿用昨天的值,带 stale_from
    m = _with_last_good(
        lambda: _make_failed("VIX", "yfinance limit"),
        cache=cache, cache_key="VIX", today=date(2026, 5, 8),
    )
    assert m.error is None  # 沿用 = 不显示 error
    assert m.current == 17.5
    assert m.prior == 16.9
    assert m.stale_from == "2026-05-07"


def test_with_last_good_no_cache_passes_through(tmp_state: Path) -> None:
    """cache=None 时维持失败原样,不沿用。"""
    m = _with_last_good(
        lambda: _make_failed("VIX"),
        cache=None, cache_key="VIX", today=date(2026, 5, 8),
    )
    assert m.error is not None
    assert m.stale_from is None


def test_with_last_good_stale_passes_through_error(tmp_state: Path) -> None:
    """缓存存在但已过 7 天 → 透传原 error,不沿用。"""
    cache = LastGoodCache(tmp_state)
    cache.put("sentiment.VIX", {"current": 17.5, "prior": 16.9, "rating": None, "unit": ""},
              today=date(2026, 4, 1))  # 5 周前
    m = _with_last_good(
        lambda: _make_failed("VIX", "still failing"),
        cache=cache, cache_key="VIX", today=date(2026, 5, 8),
    )
    assert m.error is not None  # 不沿用陈旧值
    assert m.stale_from is None
    assert "still failing" in m.error


def test_with_last_good_cache_miss_passes_through_error(tmp_state: Path) -> None:
    """缓存为空 + 失败 → 透传 error,不能凭空造数。"""
    m = _with_last_good(
        lambda: _make_failed("VIX", "first day fail"),
        cache=LastGoodCache(tmp_state),
        cache_key="VIX", today=date(2026, 5, 8),
    )
    assert m.error is not None
    assert "first day fail" in m.error
    assert m.stale_from is None


def test_with_last_good_recovery_clears_stale(tmp_state: Path) -> None:
    """先有 stale,后来恢复成功 → 新值入 cache,不再带 stale_from。"""
    cache = LastGoodCache(tmp_state)
    cache.put("sentiment.VIX", {"current": 17.5, "prior": 16.9, "rating": None, "unit": ""},
              today=date(2026, 5, 7))
    m = _with_last_good(
        lambda: _make_ok("VIX", 18.5, 17.5),
        cache=cache, cache_key="VIX", today=date(2026, 5, 8),
    )
    assert m.error is None
    assert m.current == 18.5
    assert m.stale_from is None  # 当天成功,不沿用
    assert cache.get("sentiment.VIX")[0]["current"] == 18.5  # cache 已更新


# ─── iso_date_md filter ──────────────────────────────────────────


def test_iso_date_md_strips_leading_zeros() -> None:
    assert _filter_iso_date_md("2026-05-07") == "5/7"
    assert _filter_iso_date_md("2026-12-31") == "12/31"
    assert _filter_iso_date_md("2026-01-01") == "1/1"


def test_iso_date_md_handles_garbage() -> None:
    assert _filter_iso_date_md("") == ""
    assert _filter_iso_date_md(None) == ""
    assert _filter_iso_date_md("not-a-date") == ""
    assert _filter_iso_date_md("2026-05") == ""
    assert _filter_iso_date_md("2026-XX-07") == ""


# ─── fetch_all 集成 ───────────────────────────────────────────────


def test_fetch_all_vix_cboe_success_persists(monkeypatch, tmp_state: Path) -> None:
    """VIX CBOE 成功 → metric 正常,值进 cache。"""
    cboe_payload = {
        "data": [
            {"date": "2026-05-06", "close": "16.99"},
            {"date": "2026-05-07", "close": "17.83"},
        ],
    }

    def fake_get(url: str, *_a, **_kw):
        if "cboe" in url.lower():
            return _FakeResp(cboe_payload)
        # CNN / multpl / FRED 全部模拟成失败,简化测试
        raise RuntimeError(f"unmocked: {url}")

    monkeypatch.setattr(sentiment.requests, "get", fake_get)
    # yfinance / shiller / cnn / fred 都让其失败,只关心 VIX 走 CBOE
    monkeypatch.setattr(
        sentiment, "_fetch_cnn_fear_greed",
        lambda: _make_failed("CNN Fear & Greed"),
    )
    monkeypatch.setattr(
        sentiment, "_fetch_simple_index",
        lambda *_a, **_kw: _make_failed("DXY"),
    )
    monkeypatch.setattr(
        sentiment, "_fetch_shiller_pe",
        lambda: _make_failed("Shiller PE"),
    )
    monkeypatch.setattr(
        sentiment, "_fetch_fred_hy_spread",
        lambda _key: _make_failed("FRED HY"),
    )

    bundle = sentiment.fetch_all(
        "fakefredkey", state_dir=tmp_state, today=date(2026, 5, 8),
    )
    vix = next(m for m in bundle.metrics if m.name == "VIX")
    assert vix.error is None
    assert vix.current == pytest.approx(17.83)
    assert vix.stale_from is None  # CBOE 成功,不是沿用

    # cache 持久化
    cached = LastGoodCache(tmp_state).get("sentiment.VIX")
    assert cached is not None
    assert cached[0]["current"] == pytest.approx(17.83)


def test_fetch_all_vix_cboe_fails_falls_back_to_yfinance(
    monkeypatch, tmp_state: Path,
) -> None:
    """CBOE 失败 → _fetch_simple_index 备路径(用 yfinance)。"""
    monkeypatch.setattr(
        sentiment, "_fetch_cboe_vix",
        lambda: (_ for _ in ()).throw(RuntimeError("cboe down")),
    )
    # yfinance 路径(_fetch_simple_index)给个成功值
    fake_simple = lambda ticker, name, unit="": _make_ok(name, 17.0, 16.5)  # noqa: E731
    monkeypatch.setattr(sentiment, "_fetch_simple_index", fake_simple)
    monkeypatch.setattr(
        sentiment, "_fetch_cnn_fear_greed", lambda: _make_failed("CNN Fear & Greed"),
    )
    monkeypatch.setattr(
        sentiment, "_fetch_shiller_pe", lambda: _make_failed("Shiller PE"),
    )
    monkeypatch.setattr(
        sentiment, "_fetch_fred_hy_spread", lambda _k: _make_failed("FRED HY"),
    )

    bundle = sentiment.fetch_all(
        "fakefredkey", state_dir=tmp_state, today=date(2026, 5, 8),
    )
    vix = next(m for m in bundle.metrics if m.name == "VIX")
    assert vix.error is None
    assert vix.current == pytest.approx(17.0)
    assert vix.stale_from is None  # yfinance 当天成功,不算沿用


def test_fetch_all_vix_both_fail_uses_last_good(
    monkeypatch, tmp_state: Path,
) -> None:
    """CBOE + yfinance 都挂 + cache 有昨天值 → 沿用,带 stale_from。"""
    LastGoodCache(tmp_state).put(
        "sentiment.VIX",
        {"current": 17.5, "prior": 16.9, "rating": None, "unit": ""},
        today=date(2026, 5, 7),
    )

    monkeypatch.setattr(
        sentiment, "_fetch_cboe_vix",
        lambda: (_ for _ in ()).throw(RuntimeError("cboe down")),
    )
    monkeypatch.setattr(
        sentiment, "_fetch_simple_index",
        lambda *_a, **_kw: _make_failed("VIX", "yfinance also down"),
    )
    monkeypatch.setattr(
        sentiment, "_fetch_cnn_fear_greed", lambda: _make_failed("CNN Fear & Greed"),
    )
    monkeypatch.setattr(
        sentiment, "_fetch_shiller_pe", lambda: _make_failed("Shiller PE"),
    )
    monkeypatch.setattr(
        sentiment, "_fetch_fred_hy_spread", lambda _k: _make_failed("FRED HY"),
    )

    bundle = sentiment.fetch_all(
        "fakefredkey", state_dir=tmp_state, today=date(2026, 5, 8),
    )
    vix = next(m for m in bundle.metrics if m.name == "VIX")
    assert vix.error is None
    assert vix.current == 17.5
    assert vix.stale_from == "2026-05-07"
