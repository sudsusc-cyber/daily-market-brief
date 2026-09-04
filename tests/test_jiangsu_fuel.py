from __future__ import annotations

import time
from datetime import date
from types import SimpleNamespace

import pytest

from src.collectors import jiangsu_fuel


def _entry(
    title: str,
    *,
    source: str = "第一财经",
    published: str = "2026-08-12 03:00:00",
    summary: str = "",
) -> SimpleNamespace:
    parsed = time.strptime(published, "%Y-%m-%d %H:%M:%S")
    return SimpleNamespace(
        title=title,
        summary=summary,
        link="https://news.google.com/example",
        published_parsed=parsed,
        source=SimpleNamespace(title=source),
    )


def test_fetch_only_returns_during_one_or_two_day_window(monkeypatch) -> None:
    def should_not_fetch(_target):
        raise AssertionError("窗口之外不应抓预测新闻")

    monkeypatch.setattr(jiangsu_fuel, "_fetch_forecast_entries", should_not_fetch)

    assert jiangsu_fuel.fetch(today=date(2026, 8, 5)) is None


def test_fetch_parses_jiangsu_forecast_direction_and_per_liter_amounts(monkeypatch) -> None:
    entries = [_entry(
        "油价调整最新消息：8月14日24时，预计92号汽油每升下调0.18元，"
        "95号汽油每升下调0.20元",
    )]
    monkeypatch.setattr(jiangsu_fuel, "_fetch_forecast_entries", lambda _target: entries)

    alert = jiangsu_fuel.fetch(today=date(2026, 8, 12))

    assert alert is not None
    assert alert.adjustment_date == date(2026, 8, 14)
    assert alert.days_until == 2
    assert alert.direction == "下调"
    assert "92 号约 -0.18 元/升" in alert.detail
    assert "95 号约 -0.2 元/升" in alert.detail
    assert alert.forecast_source == "第一财经"
    assert alert.forecast_url == "https://news.google.com/example"


def test_forecast_word_wins_over_historic_move_in_same_title(monkeypatch) -> None:
    entries = [_entry(
        "全国92号汽油刚涨0.59元/升，下次8月14日调价预计下调120元/吨",
        source="隆众资讯",
    )]
    monkeypatch.setattr(jiangsu_fuel, "_fetch_forecast_entries", lambda _target: entries)

    alert = jiangsu_fuel.fetch(today=date(2026, 8, 13))

    assert alert is not None
    assert alert.direction == "下调"
    assert alert.detail == "汽油约 -0.09 元/升；柴油约 -0.1 元/升"


def test_per_ton_forecast_is_converted_to_rmb_per_liter() -> None:
    assert jiangsu_fuel._detail_from_text(
        "预计下调380元/吨",
        "下调",
    ) == "汽油约 -0.28 元/升；柴油约 -0.32 元/升"

    assert jiangsu_fuel._detail_from_text(
        "预计上调200元/吨",
        "上调",
    ) == "汽油约 +0.15 元/升；柴油约 +0.17 元/升"


def test_per_ton_forecast_ignores_previous_round_with_opposite_direction() -> None:
    assert jiangsu_fuel._detail_from_text(
        "上轮下调200元/吨，本轮预计上调，幅度待定",
        "上调",
    ) == "预计上调，具体幅度待更新"

    assert jiangsu_fuel._detail_from_text(
        "上轮下调200元/吨，本轮预计上调300元/吨",
        "上调",
    ) == "汽油约 +0.22 元/升；柴油约 +0.25 元/升"


def test_per_ton_forecast_accepts_thousands_separators() -> None:
    assert jiangsu_fuel._detail_from_text(
        "预计上调1,000元/吨",
        "上调",
    ) == "汽油约 +0.74 元/升；柴油约 +0.84 元/升"

    assert jiangsu_fuel._detail_from_text(
        "预计下调1，000元/吨",
        "下调",
    ) == "汽油约 -0.74 元/升；柴油约 -0.84 元/升"


def test_tiny_per_ton_forecast_never_renders_signed_zero() -> None:
    detail = jiangsu_fuel._detail_from_text("预计下调1元/吨", "下调")

    assert detail == "汽油约 0 元/升；柴油约 0 元/升"
    assert "+0 元/升" not in detail
    assert "-0 元/升" not in detail


@pytest.mark.parametrize(("text", "expected"), [
    ("预计每升上涨0.18元", "汽柴油约 +0.18 元/升"),
    ("预计汽柴油每升上涨1至2分", "汽柴油约 +0.01 元/升 ～ +0.02 元/升"),
    ("预计上调幅度为200元/吨", "汽油约 +0.15 元/升；柴油约 +0.17 元/升"),
    ("92号汽油预计上调200元/吨", "汽油约 +0.15 元/升"),
    ("汽油上调200元/吨，柴油上调180元/吨", "汽油约 +0.15 元/升；柴油约 +0.15 元/升"),
    ("92号汽油预计上涨0.18元", "预计上调，具体幅度待更新"),
    ("上轮上涨200元/吨，本轮预计上涨", "预计上调，具体幅度待更新"),
    ("92号汽油每升预计上涨&nbsp;0.18元", "92 号约 +0.18 元/升"),
])
def test_amount_wording_and_unit_safety(text, expected) -> None:
    assert jiangsu_fuel._detail_from_text(text, "上调") == expected


def test_same_day_complete_forecast_beats_direction_only(monkeypatch) -> None:
    entries = [
        _entry("8月14日油价预计上涨", source="新华社"),
        _entry("8月14日油价预计上调幅度为200元/吨", source="隆众资讯"),
    ]
    monkeypatch.setattr(jiangsu_fuel, "_fetch_forecast_entries", lambda _: entries)
    alert = jiangsu_fuel.fetch(today=date(2026, 8, 12))
    assert alert.detail == "汽油约 +0.15 元/升；柴油约 +0.17 元/升"
    assert alert.forecast_source == "隆众资讯"


def test_newer_direction_is_not_overwritten_by_old_amount(monkeypatch) -> None:
    entries = [
        _entry("8月14日油价预计上涨", published="2026-08-13 03:00:00"),
        _entry("8月14日油价预计下调200元/吨"),
        _entry("8月14日油价预计上调500元/吨", published="2026-08-14 03:00:00"),
    ]
    monkeypatch.setattr(jiangsu_fuel, "_fetch_forecast_entries", lambda _: entries)
    alert = jiangsu_fuel.fetch(today=date(2026, 8, 13))
    assert alert.direction == "上调"
    assert "待更新" in alert.detail


def test_exact_target_date_beats_newer_low_relevance_candidate(monkeypatch) -> None:
    entries = [
        _entry(
            "8月14日国内成品油调价预计下调120元/吨",
            source="第一财经",
            published="2026-08-12 03:00:00",
        ),
        _entry(
            "国际油价上涨，国内成品油上调预期升温",
            source="未知自媒体",
            published="2026-08-13 05:00:00",
        ),
    ]
    monkeypatch.setattr(jiangsu_fuel, "_fetch_forecast_entries", lambda _target: entries)

    alert = jiangsu_fuel.fetch(today=date(2026, 8, 13))

    assert alert is not None
    assert alert.direction == "下调"
    assert alert.forecast_source == "第一财经"


def test_forecast_failure_still_renders_schedule_only_alert(monkeypatch) -> None:
    def fail(_target):
        raise RuntimeError("network unavailable")

    monkeypatch.setattr(jiangsu_fuel, "_fetch_forecast_entries", fail)
    monkeypatch.setattr(jiangsu_fuel, "_estimate_direction_from_crude", lambda _today: None)

    alert = jiangsu_fuel.fetch(today=date(2026, 8, 13))

    assert alert is not None
    assert alert.direction == "待定"
    assert alert.detail == "涨跌方向与幅度待更新"
    assert alert.forecast_source is None
    assert alert.forecast_method == "schedule_only"


def test_candidate_rejects_unrelated_oil_market_story() -> None:
    candidate = jiangsu_fuel._candidate_from_entry(
        _entry("国际原油市场供需展望：油价波动加剧"),
        date(2026, 8, 14),
    )

    assert candidate is None


def test_forecast_queries_continue_after_one_source_path_fails(monkeypatch) -> None:
    entry = _entry("8月14日国内成品油调价预计下调120元/吨")
    calls = 0

    def fetch_query(_query):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("first query unavailable")
        return [entry]

    monkeypatch.setattr(jiangsu_fuel, "_fetch_google_news_query", fetch_query)

    entries = jiangsu_fuel._fetch_forecast_entries(date(2026, 8, 14))

    assert calls == 4
    assert entries == [entry]


def test_crude_proxy_supplies_direction_when_all_news_predictions_fail(monkeypatch) -> None:
    monkeypatch.setattr(jiangsu_fuel, "_fetch_forecast_entries", lambda _target: [])
    monkeypatch.setattr(
        jiangsu_fuel,
        "_estimate_direction_from_crude",
        lambda _today: ("下调", -0.024),
    )

    alert = jiangsu_fuel.fetch(today=date(2026, 8, 13))

    assert alert is not None
    assert alert.direction == "下调"
    assert alert.detail == "预计下调，具体幅度待更新"
    assert alert.forecast_method == "crude_proxy"


def test_tuesday_window_is_announced_on_previous_saturday(monkeypatch) -> None:
    monkeypatch.setattr(jiangsu_fuel, "_fetch_forecast_entries", lambda _target: [])
    monkeypatch.setattr(jiangsu_fuel, "_estimate_direction_from_crude", lambda _today: None)

    alert = jiangsu_fuel.fetch(today=date(2026, 1, 31))

    assert alert is not None
    assert alert.adjustment_date == date(2026, 2, 3)
    assert alert.days_until == 3
    assert alert.forecast_method == "schedule_only"


def test_holiday_calendar_uses_secondary_mirror_when_primary_fails(monkeypatch) -> None:
    payload = {
        "year": 2027,
        "days": [
            {"name": "元旦", "date": "2027-01-01", "isOffDay": True},
            {"name": "补班", "date": "2027-01-09", "isOffDay": False},
        ],
    }
    calls = 0

    def fetch_json(_url):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("raw github unavailable")
        return payload

    monkeypatch.setattr(jiangsu_fuel, "_fetch_json", fetch_json)

    overrides = jiangsu_fuel._fetch_holiday_overrides(2027)

    assert calls == 2
    assert overrides[date(2027, 1, 1)] is False
    assert overrides[date(2027, 1, 9)] is True


def test_future_windows_roll_forward_by_ten_china_workdays(monkeypatch) -> None:
    def calendar(year):
        if year == 2027:
            return {date(2027, 1, 1): False}
        return {}

    monkeypatch.setattr(jiangsu_fuel, "_fetch_holiday_overrides", calendar)

    assert jiangsu_fuel._next_calculated_window(date(2026, 12, 25)) == date(2027, 1, 8)
