"""
美股节假日判断(M6)。

逻辑:
- 北京时间今天发邮件反映的是"美股最近一个交易日"的数据(通常是北京前一天对应的美东交易日)。
- 若北京"今天"对应的美股目标日期(=今天日历日 - 1 天)是周末或 NYSE 节假日,则跳过发送。
- **节假日表纯算法生成**,每年自动,无需人工维护。

NYSE 全休日(10 个,2022 年起含 Juneteenth):
  1. 元旦 1/1(观察日)
  2. MLK Day:1 月第 3 个周一
  3. Presidents' Day:2 月第 3 个周一
  4. Good Friday:复活节前一周五(Anonymous Gregorian Algorithm 算复活节)
  5. Memorial Day:5 月最后一个周一
  6. Juneteenth 6/19(观察日,2022 起)
  7. Independence Day 7/4(观察日)
  8. Labor Day:9 月第 1 个周一
  9. Thanksgiving:11 月第 4 个周四
 10. Christmas 12/25(观察日)

NYSE 观察日规则:周六→前一周五休市,周日→后一周一休市。

不含半日交易(感恩节后周五等),M6 仅按全休跳过发送。

主入口:should_send_today(today_bj) -> tuple[bool, str]
  - 返回 (True/False, 原因);main.py 启动时调用,False 时直接 return 0
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

logger = logging.getLogger(__name__)


def _nth_weekday(year: int, month: int, n: int, weekday: int) -> date:
    """该月第 n 个 weekday(0=周一,3=周四)。"""
    first = date(year, month, 1)
    days_offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=days_offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    """该月最后一个 weekday(0=周一)。"""
    first_of_next = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    last_day = first_of_next - timedelta(days=1)
    days_back = (last_day.weekday() - weekday) % 7
    return last_day - timedelta(days=days_back)


def _easter_sunday(year: int) -> date:
    """Anonymous Gregorian Algorithm(Meeus/Jones/Butcher);适用于 1583-4099 公历年。"""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ell) // 451
    month = (h + ell - 7 * m + 114) // 31
    day = ((h + ell - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _observed(d: date) -> date:
    """NYSE 观察日规则:周六→前一周五,周日→后一周一,工作日不变。"""
    if d.weekday() == 5:  # 周六
        return d - timedelta(days=1)
    if d.weekday() == 6:  # 周日
        return d + timedelta(days=1)
    return d


def compute_us_holidays(year: int) -> set[date]:
    """指定年份 NYSE 全休节假日集合(纯算法,无外部依赖)。"""
    holidays: set[date] = {
        _observed(date(year, 1, 1)),                # 元旦
        _nth_weekday(year, 1, 3, 0),                # MLK Day
        _nth_weekday(year, 2, 3, 0),                # Presidents' Day
        _easter_sunday(year) - timedelta(days=2),   # Good Friday
        _last_weekday(year, 5, 0),                  # Memorial Day
        _observed(date(year, 7, 4)),                # Independence Day
        _nth_weekday(year, 9, 1, 0),                # Labor Day
        _nth_weekday(year, 11, 4, 3),               # Thanksgiving
        _observed(date(year, 12, 25)),              # Christmas
    }
    if year >= 2022:
        # Juneteenth 自 2022 年起成为 NYSE 全休日(联邦假日 2021 年立法)
        holidays.add(_observed(date(year, 6, 19)))
    return holidays


def is_us_market_open(d: date) -> bool:
    """美股该日是否开盘:工作日 + 非 NYSE 节假日。"""
    if d.weekday() >= 5:  # 周六(5) / 周日(6)
        return False
    return d not in compute_us_holidays(d.year)


def should_send_today(today_bj: date) -> tuple[bool, str]:
    """
    今天(北京)是否应该发邮件。

    规则:邮件反映"美股最近一个交易日"的数据,目标日期 = today_bj - 1 day。
    若该目标日期是美股休市日(周末/节假日)→ 跳过发送。
    """
    target = today_bj - timedelta(days=1)
    if not is_us_market_open(target):
        return False, f"美股 {target.isoformat()} 休市(周末/节假日),今日跳过"
    return True, f"美股 {target.isoformat()} 正常交易,继续发送"
