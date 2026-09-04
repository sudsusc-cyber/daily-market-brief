"""Exchange-specific observation checks, independent of signal calculations."""

from datetime import UTC, date, datetime, timedelta
from functools import lru_cache

import exchange_calendars as xcals
import pandas as pd


@lru_cache(maxsize=8)
def calendar(symbol: str, year: int):
    return xcals.get_calendar("XHKG" if symbol.endswith(".HK") else "XNYS",
                              start=f"{year - 6}-01-01", end=f"{year + 1}-12-31")


def latest_closed_session(symbol: str, now: datetime) -> date:
    cal = calendar(symbol, now.year)
    closed = cal.schedule[cal.schedule["close"] <= pd.Timestamp(now)]
    if closed.empty:
        raise ValueError("交易日历没有已结束的交易日")
    return closed.index[-1].date()


def validate_history(index, *, symbol: str, interval: str, now: datetime) -> date:
    if not isinstance(index, pd.DatetimeIndex) or index.empty:
        raise ValueError("行情缺少可验证时间戳")
    if index.hasnans or not index.is_unique or not index.is_monotonic_increasing:
        raise ValueError("行情日期缺失、重复或乱序")
    cal = calendar(symbol, now.year)
    local_index = index.tz_convert(cal.tz) if index.tz is not None else index
    observed = local_index[-1].date()
    expected = latest_closed_session(symbol, now)
    minimum = expected - timedelta(days=expected.weekday()) if interval == "1wk" else expected
    if not minimum <= observed <= now.astimezone(cal.tz).date():
        raise ValueError(f"行情日期过期或超前: {observed}; 最近交易日 {expected}")
    if interval == "1d" and not cal.is_session(observed.isoformat()):
        raise ValueError("日线日期不是该交易所交易日")
    return observed


def validate_quote(timestamp, *, symbol: str, now: datetime) -> date:
    if isinstance(timestamp, bool):
        raise ValueError("行情报价时间戳无效")
    try:
        observed = datetime.fromtimestamp(float(timestamp), UTC)
    except (TypeError, ValueError, OSError, OverflowError) as exc:
        raise ValueError("行情缺少有效报价时间戳") from exc
    cal = calendar(symbol, now.year)
    day = observed.astimezone(cal.tz).date()
    if observed > now + timedelta(minutes=5) or day < latest_closed_session(symbol, now):
        raise ValueError("行情报价时间过期或超前")
    if not cal.is_session(day.isoformat()):
        raise ValueError("行情报价不属于有效交易日")
    return day
