"""江苏成品油调价预告。

目标不是提前冒充官方定价，而是在国内成品油调价窗口前 1—2 天给出一条
可核验的江苏本地提醒：调价时间来自年度窗口表，方向和幅度来自近期媒体/行业
预测；最终价格仍以江苏省发展改革委当天发布的公告为准。

国家成品油价格原则上每 10 个工作日调整一次。2026 年窗口日期按公开调价日历
固化为确定性兜底；后续年份若尚未维护，则从近期 Google News 结果中发现明确的
未来调价日期。只有距离窗口 1 或 2 个自然日时才抓取预测和返回展示对象，避免
日常邮件出现无关的长期倒计时。
"""

from __future__ import annotations

import logging
import re
import urllib.parse
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from src.utils.fetch_rss import fetch_rss
from src.utils.retry import retry
from src.utils.secrets import redact_secrets

logger = logging.getLogger(__name__)

JIANGSU_OFFICIAL_NOTICES_URL = "https://fzggw.jiangsu.gov.cn/col/col284/index.html"

# 2026 年公开调价日历。窗口均于当日 24 时开启；实际可因机制触发暂停、延迟或搁浅。
_WINDOWS_BY_YEAR: dict[int, tuple[tuple[int, int], ...]] = {
    2026: (
        (1, 6), (1, 20), (2, 3), (2, 24), (3, 9), (3, 23),
        (4, 7), (4, 21), (5, 8), (5, 21), (6, 4), (6, 18),
        (7, 3), (7, 17), (7, 31), (8, 14), (8, 28), (9, 11),
        (9, 24), (10, 15), (10, 29), (11, 12), (11, 26),
        (12, 10), (12, 24),
    ),
}

_TRUSTED_SOURCES = {
    "新华社": 10,
    "新华网": 10,
    "央视新闻": 10,
    "中国新闻网": 9,
    "中新网": 9,
    "经济参考报": 9,
    "证券时报": 8,
    "上海证券报": 8,
    "第一财经": 8,
    "界面新闻": 8,
    "Jiemian.com": 8,
    "新京报": 8,
    "中国基金报": 7,
    "每日经济新闻": 7,
    "21财经": 7,
    "澎湃新闻": 8,
    "thepaper.cn": 8,
    "隆众资讯": 9,
    "卓创资讯": 9,
    "金联创": 9,
}

_DIRECTION_WORDS = ("上调", "下调", "上涨", "下跌", "提高", "降低", "涨", "跌")
_LOWER_WORDS = {"下调", "下跌", "降低", "跌"}


@dataclass(frozen=True)
class ForecastCandidate:
    title: str
    source: str
    url: str
    published_at: datetime
    direction: str
    detail: str
    mentions_target_date: bool


@dataclass(frozen=True)
class JiangsuFuelAlert:
    adjustment_date: date
    days_until: int
    direction: str
    detail: str
    forecast_source: str | None = None
    forecast_url: str | None = None
    forecast_title: str | None = None
    official_url: str = JIANGSU_OFFICIAL_NOTICES_URL


def _year_windows(year: int) -> list[date]:
    return [date(year, month, day) for month, day in _WINDOWS_BY_YEAR.get(year, ())]


def _next_known_window(today: date) -> date | None:
    candidates = [window for year in (today.year, today.year + 1)
                  for window in _year_windows(year) if window >= today]
    return min(candidates) if candidates else None


def _entry_datetime(entry: object) -> datetime | None:
    parsed = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if not parsed:
        return None
    return datetime(*parsed[:6], tzinfo=UTC)


def _entry_source(entry: object) -> str:
    source = getattr(getattr(entry, "source", None), "title", "")
    return str(source or "Google News").strip()


def _direction_from_text(text: str) -> str | None:
    if re.search(r"搁浅|不作调整|不调整", text):
        return "搁浅"

    # 一条标题可能同时回顾上一轮“刚涨”并预告本轮“预计下调”；优先信任预计句。
    forecast = re.search(
        r"(?:预计|预期|或将|大概率|可能).{0,32}?(上调|下调|上涨|下跌|提高|降低|涨|跌)",
        text,
    )
    word = forecast.group(1) if forecast else next(
        (candidate for candidate in _DIRECTION_WORDS if candidate in text),
        None,
    )
    if word is None:
        return None
    return "下调" if word in _LOWER_WORDS else "上调"


def _signed_amount(direction: str, amount: str, unit: str) -> str:
    value = float(amount)
    rendered = f"{value:.2f}".rstrip("0").rstrip(".")
    sign = "-" if direction == "下调" else "+"
    return f"{sign}{rendered} 元/{unit}"


def _detail_from_text(text: str, direction: str) -> str:
    if direction == "搁浅":
        return "预计不作调整"

    details: list[str] = []
    # 支持“92号汽油每升下调0.18元”和“92号汽油下调0.18元/升”两种顺序。
    grade_pattern = re.compile(
        r"(89|92|95|98|0)\s*[号#]?(?:汽油|柴油)?.{0,12}?"
        r"(?:每升)?(?:预计|或)?\s*(上调|下调|上涨|下跌|提高|降低|涨|跌)"
        r"\s*(\d+(?:\.\d+)?)\s*元(?:/升)?"
    )
    for grade, word, amount in grade_pattern.findall(text):
        item_direction = "下调" if word in _LOWER_WORDS else "上调"
        # 标题可能先回顾上一轮“刚涨”，再预告本轮“预计下调”。只展示和本轮
        # 总方向一致的分油号幅度，避免把旧轮次的数字拼到新轮次上。
        if item_direction != direction:
            continue
        label = f"{grade} 号" if grade != "0" else "0 号柴油"
        detail = f"{label}约 {_signed_amount(item_direction, amount, '升')}"
        if detail not in details:
            details.append(detail)

    ton_match = re.search(
        r"(?:预计|或)?\s*(上调|下调|上涨|下跌|提高|降低|涨|跌)"
        r".{0,8}?(\d+(?:\.\d+)?)\s*元(?:/|每)吨",
        text,
    )
    if ton_match and not details:
        word, amount = ton_match.groups()
        item_direction = "下调" if word in _LOWER_WORDS else "上调"
        details.append(f"汽柴油约 {_signed_amount(item_direction, amount, '吨')}")

    if details:
        return "；".join(details[:3])
    return f"预计{direction}，具体幅度待更新"


def _mentions_date(text: str, target: date) -> bool:
    return bool(re.search(rf"{target.month}\s*月\s*{target.day}\s*日", text))


def _candidate_from_entry(entry: object, target: date) -> ForecastCandidate | None:
    published_at = _entry_datetime(entry)
    title = str(getattr(entry, "title", "") or "").strip()
    summary = str(getattr(entry, "summary", "") or "").strip()
    url = str(getattr(entry, "link", "") or "").strip()
    if published_at is None or not title or not url:
        return None

    text = f"{title} {summary}"
    if all(keyword not in text for keyword in ("油价", "成品油", "汽油", "柴油")):
        return None
    direction = _direction_from_text(text)
    if direction is None:
        return None
    return ForecastCandidate(
        title=title,
        source=_entry_source(entry),
        url=url,
        published_at=published_at,
        direction=direction,
        detail=_detail_from_text(text, direction),
        mentions_target_date=_mentions_date(text, target),
    )


def _candidate_score(candidate: ForecastCandidate, target: date) -> tuple[int, float]:
    source_score = _TRUSTED_SOURCES.get(candidate.source, 0)
    date_score = 12 if candidate.mentions_target_date else 0
    # 调价日前后新闻很多。只取调价前发布的预测，避免误用当天正式结果或旧轮次。
    before_target = candidate.published_at.date() <= target
    timing_score = 3 if before_target else -20
    return source_score + date_score + timing_score, candidate.published_at.timestamp()


@retry(max_attempts=2, base_delay=1.0)
def _fetch_forecast_entries(target: date) -> list[object]:
    query = f"{target.month}月{target.day}日 国内成品油 调价 预计 when:7d"
    encoded = urllib.parse.quote(query)
    url = (
        f"https://news.google.com/rss/search?q={encoded}"
        "&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
    )
    return list(fetch_rss(url).entries or [])


@retry(max_attempts=2, base_delay=1.0)
def _discover_window_entries() -> list[object]:
    query = urllib.parse.quote("下一次 国内成品油 调价窗口 when:7d")
    url = (
        f"https://news.google.com/rss/search?q={query}"
        "&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
    )
    return list(fetch_rss(url).entries or [])


def _discover_next_window(today: date) -> date | None:
    try:
        entries = _discover_window_entries()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "jiangsu_fuel.window_discovery_failed exc_type=%s msg=%s",
            type(exc).__name__, redact_secrets(str(exc))[:160],
        )
        return None

    candidates: list[date] = []
    for entry in entries:
        text = f"{getattr(entry, 'title', '')} {getattr(entry, 'summary', '')}"
        for month, day in re.findall(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日", text):
            for year in (today.year, today.year + 1):
                try:
                    candidate = date(year, int(month), int(day))
                except ValueError:
                    continue
                if today <= candidate <= today + timedelta(days=30):
                    candidates.append(candidate)
    return min(candidates) if candidates else None


def fetch(*, today: date) -> JiangsuFuelAlert | None:
    """在调价窗口前 1—2 天返回预告，其余日期返回 ``None``。"""
    target = _next_known_window(today) or _discover_next_window(today)
    if target is None:
        logger.info("jiangsu_fuel.no_upcoming_window today=%s", today.isoformat())
        return None

    days_until = (target - today).days
    if days_until not in (1, 2):
        logger.info(
            "jiangsu_fuel.outside_alert_window target=%s days_until=%d",
            target.isoformat(), days_until,
        )
        return None

    try:
        entries = _fetch_forecast_entries(target)
        candidates = [
            candidate for entry in entries
            if (candidate := _candidate_from_entry(entry, target)) is not None
            and (target - timedelta(days=7)) <= candidate.published_at.date() <= target
        ]
        best = max(candidates, key=lambda item: _candidate_score(item, target), default=None)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "jiangsu_fuel.forecast_failed target=%s exc_type=%s msg=%s",
            target.isoformat(), type(exc).__name__, redact_secrets(str(exc))[:160],
        )
        best = None

    if best is None:
        logger.info("jiangsu_fuel.schedule_only target=%s", target.isoformat())
        return JiangsuFuelAlert(
            adjustment_date=target,
            days_until=days_until,
            direction="待定",
            detail="涨跌方向与幅度待更新",
        )

    logger.info(
        "jiangsu_fuel.forecast target=%s direction=%s source=%s",
        target.isoformat(), best.direction, best.source,
    )
    return JiangsuFuelAlert(
        adjustment_date=target,
        days_until=days_until,
        direction=best.direction,
        detail=best.detail,
        forecast_source=best.source,
        forecast_url=best.url,
        forecast_title=best.title,
    )
