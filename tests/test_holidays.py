"""tests/test_holidays.py — M6 美股节假日预检测试(纯算法)。"""

from __future__ import annotations

from datetime import date

from src.utils.holidays import (
    _easter_sunday,
    compute_us_holidays,
    is_us_market_open,
    should_send_today,
)


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

    def test_saturday_new_year_has_no_friday_observance(self) -> None:
        """NYSE official calendar: no observed holiday for Saturday Jan 1."""
        assert is_us_market_open(date(2021, 12, 31)) is True
        assert is_us_market_open(date(2027, 12, 31)) is True
        assert is_us_market_open(date(2032, 12, 31)) is True


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


class TestComputeUsHolidays:
    """验证算法对 2024-2028 各年节假日的准确性,与 NYSE 官方表对照。"""

    def test_easter_known_dates(self) -> None:
        # 复活节算法验证(若该函数错误,Good Friday 全错)
        assert _easter_sunday(2024) == date(2024, 3, 31)
        assert _easter_sunday(2025) == date(2025, 4, 20)
        assert _easter_sunday(2026) == date(2026, 4, 5)
        assert _easter_sunday(2027) == date(2027, 3, 28)
        assert _easter_sunday(2028) == date(2028, 4, 16)

    def test_2024_official_nyse(self) -> None:
        # NYSE 2024 官方表:1/1, 1/15, 2/19, 3/29, 5/27, 6/19, 7/4, 9/2, 11/28, 12/25
        expected = {
            date(2024, 1, 1), date(2024, 1, 15), date(2024, 2, 19),
            date(2024, 3, 29), date(2024, 5, 27), date(2024, 6, 19),
            date(2024, 7, 4), date(2024, 9, 2), date(2024, 11, 28),
            date(2024, 12, 25),
        }
        assert compute_us_holidays(2024) == expected

    def test_2025_official_nyse(self) -> None:
        # NYSE 2025:1/1, 1/20, 2/17, 4/18, 5/26, 6/19, 7/4, 9/1, 11/27, 12/25
        expected = {
            date(2025, 1, 1), date(2025, 1, 20), date(2025, 2, 17),
            date(2025, 4, 18), date(2025, 5, 26), date(2025, 6, 19),
            date(2025, 7, 4), date(2025, 9, 1), date(2025, 11, 27),
            date(2025, 12, 25),
        }
        assert compute_us_holidays(2025) == expected

    def test_2026_official_nyse(self) -> None:
        # NYSE 2026:1/1, 1/19, 2/16, 4/3, 5/25, 6/19, 7/3(observed), 9/7, 11/26, 12/25
        expected = {
            date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16),
            date(2026, 4, 3), date(2026, 5, 25), date(2026, 6, 19),
            date(2026, 7, 3), date(2026, 9, 7), date(2026, 11, 26),
            date(2026, 12, 25),
        }
        assert compute_us_holidays(2026) == expected

    def test_2027_official_nyse(self) -> None:
        # NYSE 2027:1/1, 1/18, 2/15, 3/26, 5/31, 6/18(observed,周六前移), 7/5(observed,周日后移),
        #          9/6, 11/25, 12/24(observed,周六前移)
        expected = {
            date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15),
            date(2027, 3, 26), date(2027, 5, 31), date(2027, 6, 18),
            date(2027, 7, 5), date(2027, 9, 6), date(2027, 11, 25),
            date(2027, 12, 24),
        }
        assert compute_us_holidays(2027) == expected

    def test_pre_2022_no_juneteenth(self) -> None:
        # Juneteenth 自 2022 起才是 NYSE 节假日
        assert date(2021, 6, 18) not in compute_us_holidays(2021)
        assert date(2021, 6, 19) not in compute_us_holidays(2021)
        # 2022 起应该包含
        assert date(2022, 6, 20) in compute_us_holidays(2022)  # 6/19 周日 → 6/20 观察

    def test_count_per_year(self) -> None:
        # Saturday New Year's Day has no observed weekday holiday.
        for year in range(2022, 2030):
            expected = 9 if date(year, 1, 1).weekday() == 5 else 10
            assert len(compute_us_holidays(year)) == expected, f"年份 {year} 节假日数错"

    def test_far_future_year_runs(self) -> None:
        # 2050 年也能算出来,不会崩
        out = compute_us_holidays(2050)
        assert isinstance(out, set)
        assert len(out) == 9
