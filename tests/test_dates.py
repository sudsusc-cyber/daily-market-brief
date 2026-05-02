"""单元测试:时间窗口与时区转换。"""

from __future__ import annotations

from datetime import datetime, timedelta

from src.utils.dates import (
    BEIJING,
    UTC,
    last_24h_window,
    now_beijing,
    now_beijing_human,
    now_utc,
    to_beijing,
    yesterday_beijing_window,
)


class TestNow:
    def test_now_utc_aware(self) -> None:
        dt = now_utc()
        assert dt.tzinfo is UTC

    def test_now_beijing_aware(self) -> None:
        dt = now_beijing()
        assert dt.tzinfo is BEIJING

    def test_now_beijing_human_format(self) -> None:
        s = now_beijing_human()
        # 形如 "2026 年 4 月 30 日(星期X)10:53"
        assert "年" in s and "月" in s and "日(" in s and "星期" in s
        # 不应包含西文日期
        assert "/" not in s


class TestToBeijing:
    def test_naive_treated_as_utc(self) -> None:
        naive = datetime(2026, 4, 30, 0, 0, 0)
        bj = to_beijing(naive)
        assert bj.hour == 8 and bj.tzinfo is BEIJING  # UTC 0 时 → 北京 8 时

    def test_aware_utc(self) -> None:
        aware = datetime(2026, 4, 30, 0, 0, 0, tzinfo=UTC)
        bj = to_beijing(aware)
        assert bj.hour == 8 and bj.tzinfo is BEIJING


class TestWindows:
    def test_yesterday_beijing_window_is_24h(self) -> None:
        start, end = yesterday_beijing_window()
        # 24 小时长度
        assert end - start == timedelta(hours=24)
        # end 转北京时间应该是当日 00:00
        assert end.astimezone(BEIJING).hour == 0
        assert end.astimezone(BEIJING).minute == 0

    def test_last_24h_window_close_to_now(self) -> None:
        start, end = last_24h_window()
        assert end - start == timedelta(hours=24)
        # end 与 now_utc() 相差应小于 1 秒
        assert abs((end - now_utc()).total_seconds()) < 2
