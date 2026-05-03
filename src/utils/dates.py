"""
时区与日期工具。

约定(来自 ADR-0001 §3 / PLAN 第 9 节边界情况):
  - 内部数据计算 / 比对一律用 UTC
  - 用户视角的 "今天 / 昨日" 与邮件展示一律用北京时间(Asia/Shanghai)
  - 喂给 LLM 的 system prompt 必须显式注入北京时间(M4 起兑现)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

BEIJING = ZoneInfo("Asia/Shanghai")

_WEEKDAYS_CN = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]


def now_utc() -> datetime:
    """当前 UTC 时间(aware)"""
    return datetime.now(UTC)


def now_beijing() -> datetime:
    """当前北京时间(aware)"""
    return datetime.now(BEIJING)


def now_beijing_human() -> str:
    """LLM prompt 与日志用的人类可读北京时间字符串。

    形如 "2026 年 4 月 30 日(星期四)10:53"。
    M4 起 processors/llm_client.py 必须把它注入 system prompt 末尾。
    """
    bj = now_beijing()
    return f"{bj.year} 年 {bj.month} 月 {bj.day} 日({_WEEKDAYS_CN[bj.weekday()]}){bj:%H:%M}"


def yesterday_beijing_window() -> tuple[datetime, datetime]:
    """返回 (北京昨日 00:00, 北京今日 00:00) 的 UTC aware datetime。

    用于"昨日新闻"窗口计算——以用户视角(北京时间)的整日为准。
    """
    bj_now = now_beijing()
    bj_today_start = bj_now.replace(hour=0, minute=0, second=0, microsecond=0)
    bj_yesterday_start = bj_today_start - timedelta(days=1)
    return bj_yesterday_start.astimezone(UTC), bj_today_start.astimezone(UTC)


def last_24h_window() -> tuple[datetime, datetime]:
    """返回 (24 小时前, 现在) 的 UTC aware datetime。

    用于"过去 24 小时"窗口——比"昨日整日"更宽,用于人物发言之类的滚动统计。
    """
    end = now_utc()
    return end - timedelta(hours=24), end


def to_beijing(dt: datetime) -> datetime:
    """把任意 aware/naive datetime 转换为北京时间。naive 视为 UTC。"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(BEIJING)
