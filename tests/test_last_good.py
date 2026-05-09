"""单元测试:src/utils/last_good.py — LastGoodCache."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from src.utils.last_good import DEFAULT_MAX_AGE_DAYS, LastGoodCache


@pytest.fixture
def tmp_state(tmp_path: Path) -> Path:
    state = tmp_path / "state"
    state.mkdir()
    return state


def test_get_when_file_missing_returns_none(tmp_state: Path) -> None:
    cache = LastGoodCache(tmp_state)
    assert cache.get("sentiment.VIX") is None


def test_put_then_get_roundtrip(tmp_state: Path) -> None:
    cache = LastGoodCache(tmp_state)
    today = date(2026, 5, 8)
    cache.put("sentiment.VIX", {"current": 17.5, "prior": 16.9}, today=today)

    cached = cache.get("sentiment.VIX")
    assert cached is not None
    value, saved_at = cached
    assert value == {"current": 17.5, "prior": 16.9}
    assert saved_at == "2026-05-08"


def test_put_persists_across_instances(tmp_state: Path) -> None:
    """同一 state_dir 上两个独立实例能互相读到对方写入。"""
    today = date(2026, 5, 8)
    LastGoodCache(tmp_state).put("sentiment.VIX", 17.5, today=today)

    cache2 = LastGoodCache(tmp_state)
    cached = cache2.get("sentiment.VIX")
    assert cached is not None
    assert cached[0] == 17.5


def test_multiple_keys_independent(tmp_state: Path) -> None:
    cache = LastGoodCache(tmp_state)
    today = date(2026, 5, 8)
    cache.put("sentiment.VIX", 17.5, today=today)
    cache.put("sentiment.DXY", 97.84, today=today)

    assert cache.get("sentiment.VIX")[0] == 17.5
    assert cache.get("sentiment.DXY")[0] == 97.84


def test_put_overwrites_existing_key(tmp_state: Path) -> None:
    cache = LastGoodCache(tmp_state)
    cache.put("sentiment.VIX", 17.5, today=date(2026, 5, 7))
    cache.put("sentiment.VIX", 18.2, today=date(2026, 5, 8))

    cached = cache.get("sentiment.VIX")
    assert cached == (18.2, "2026-05-08")


def test_corrupted_json_returns_none_no_raise(tmp_state: Path) -> None:
    """文件损坏时 get 返回 None,不抛异常。"""
    (tmp_state / "last_good.json").write_text("{this is not valid json", encoding="utf-8")
    cache = LastGoodCache(tmp_state)
    assert cache.get("sentiment.VIX") is None


def test_malformed_entry_silently_dropped(tmp_state: Path) -> None:
    """文件存在但某个 entry 结构不对,该 entry 视为不存在,其他 entry 仍可读。"""
    (tmp_state / "last_good.json").write_text(
        '{"sentiment.VIX": {"value": 17.5, "saved_at": "2026-05-08"},'
        '"sentiment.bad": "not a dict"}',
        encoding="utf-8",
    )
    cache = LastGoodCache(tmp_state)
    assert cache.get("sentiment.VIX") == (17.5, "2026-05-08")
    assert cache.get("sentiment.bad") is None


def test_is_stale_within_window(tmp_state: Path) -> None:
    today = date(2026, 5, 8)
    # 7 天前(刚好等于 max_age_days),不算 stale(> 不是 >=)
    assert not LastGoodCache.is_stale("2026-05-01", today=today, max_age_days=7)
    # 1 天前
    assert not LastGoodCache.is_stale("2026-05-07", today=today, max_age_days=7)


def test_is_stale_outside_window(tmp_state: Path) -> None:
    today = date(2026, 5, 8)
    # 8 天前(> max_age_days)
    assert LastGoodCache.is_stale("2026-04-30", today=today, max_age_days=7)


def test_is_stale_default_max_age(tmp_state: Path) -> None:
    """默认 max_age_days = 7。"""
    today = date(2026, 5, 15)
    assert DEFAULT_MAX_AGE_DAYS == 7
    # 7 天前 — 边界
    assert not LastGoodCache.is_stale("2026-05-08", today=today)
    # 8 天前 — 越过
    assert LastGoodCache.is_stale("2026-05-07", today=today)


def test_is_stale_unparseable_date_treated_as_stale(tmp_state: Path) -> None:
    """损坏的 saved_at 视为已过期(保守)。"""
    today = date(2026, 5, 8)
    assert LastGoodCache.is_stale("not-a-date", today=today)
    assert LastGoodCache.is_stale("", today=today)


def test_atomic_write_no_partial_file(tmp_state: Path) -> None:
    """正常 put 后,只存在最终文件,没有 .tmp 残留。"""
    cache = LastGoodCache(tmp_state)
    cache.put("sentiment.VIX", 17.5, today=date(2026, 5, 8))

    assert (tmp_state / "last_good.json").exists()
    assert not (tmp_state / "last_good.tmp").exists()


def test_value_can_be_complex_dict(tmp_state: Path) -> None:
    """value 接受任意 JSON 可序列化对象(dict / list / 嵌套)。"""
    cache = LastGoodCache(tmp_state)
    payload = {
        "current": 17.5,
        "prior": 16.9,
        "rating": "greed",
        "history": [16.9, 17.0, 17.5],
    }
    cache.put("sentiment.VIX", payload, today=date(2026, 5, 8))
    assert cache.get("sentiment.VIX")[0] == payload
