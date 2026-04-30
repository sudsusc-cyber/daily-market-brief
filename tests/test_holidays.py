"""tests/test_holidays.py — M6 美股节假日预检测试。"""

from __future__ import annotations

from datetime import date

from src.utils.holidays import is_us_market_open, should_send_today


class TestIsUsMarketOpen:
    def test_weekday_normal_open(self) -> None:
        # 2026-04-15 周三,非节假日
        assert is_us_market_open(date(2026, 4, 15)) is True

    def test_saturday_closed(self) -> None:
        assert is_us_market_open(date(2026, 4, 18)) is False

    def test_sunday_closed(self) -> None:
        assert is_us_market_open(date(2026, 4, 19)) is False

    def test_new_year_2026_closed(self) -> None:
        # 2026-01-01 周四元旦
        assert is_us_market_open(date(2026, 1, 1)) is False

    def test_independence_day_2026_observed(self) -> None:
        # 7/4 是周六,观察日 7/3 周五休市
        assert is_us_market_open(date(2026, 7, 3)) is False
        # 7/4 周六本就休市
        assert is_us_market_open(date(2026, 7, 4)) is False

    def test_thanksgiving_2026(self) -> None:
        # 11/26 周四感恩节
        assert is_us_market_open(date(2026, 11, 26)) is False
        # 11/27 周五是半日交易,M6 仍当作开盘日(只跳全休)
        assert is_us_market_open(date(2026, 11, 27)) is True


class TestShouldSendToday:
    def test_send_after_normal_trading_day(self) -> None:
        # 北京 周三 4/15 → 美股目标 4/14 周二,正常 → 发
        ok, reason = should_send_today(date(2026, 4, 15))
        assert ok is True
        assert "正常交易" in reason

    def test_skip_after_us_holiday(self) -> None:
        # 北京 周二 1/20 → 美股目标 1/19 周一(MLK Day) → 跳过
        ok, reason = should_send_today(date(2026, 1, 20))
        assert ok is False
        assert "节假日" in reason or "休市" in reason

    def test_skip_after_weekend(self) -> None:
        # 北京 周一 4/20 → 美股目标 4/19 周日 → 跳过
        # (注:cron 不会在周一触发,但函数本身应正确判断)
        ok, _reason = should_send_today(date(2026, 4, 20))
        assert ok is False

    def test_skip_after_christmas(self) -> None:
        # 北京 周六 12/26 → 美股目标 12/25 周五圣诞 → 跳过
        ok, _reason = should_send_today(date(2026, 12, 26))
        assert ok is False
