"""江苏成品油调价预告。

目标不是提前冒充官方定价，而是在国内成品油调价窗口前给出一条不会因单一
来源故障而消失的提醒。调价时间优先来自年度窗口表，后续年份按国务院放假安排
和“每 10 个工作日”机制滚动生成；方向和幅度优先来自近期媒体/行业预测，失败
时用 Brent/WTI 均价代理估算方向，再失败仍展示调价时间和“方向待确认”。

国家成品油价格原则上每 10 个工作日调整一次。正常只在距离窗口 1 或 2 个自然日
时返回展示对象；若调价日为周二，前两天恰逢本系统不发刊的周日、周一，则提前
到周六（3 天前）展示一次，确保可用发刊日不漏报。
"""

from __future__ import annotations

import html
import logging
import math
import re
import urllib.parse
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import requests
import yfinance as yf

from src.utils.fetch_rss import fetch_rss
from src.utils.retry import retry
from src.utils.secrets import redact_secrets

logger = logging.getLogger(__name__)

JIANGSU_OFFICIAL_NOTICES_URL = "https://fzggw.jiangsu.gov.cn/col/col284/index.html"

_CALENDAR_URLS = (
    "https://raw.githubusercontent.com/NateScarlet/holiday-cn/master/{year}.json",
    "https://cdn.jsdelivr.net/gh/NateScarlet/holiday-cn@master/{year}.json",
    "https://timor.tech/api/holiday/year/{year}",
)
_CALENDAR_USER_AGENT = "daily-market-brief/0.1"
_MAX_CALENDAR_BYTES = 1024 * 1024
_CRUDE_PROXY_SYMBOLS = ("BZ=F", "CL=F")
_FRED_CRUDE_SERIES = ("DCOILBRENTEU", "DCOILWTICO")
_CRUDE_DIRECTION_THRESHOLD = 0.003
# 吨价换算为终端更直观的元/升。成品油密度会随油号、温度和批次变化，
# 因此这里只做预告展示用的近似换算，不冒充加油站最终挂牌价。
_GASOLINE_KG_PER_LITER = 0.74
_DIESEL_KG_PER_LITER = 0.84

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
_AMOUNT_RE = r"(?:\d{1,3}(?:[,，]\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"


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
    forecast_method: str = "schedule_only"
    official_url: str = JIANGSU_OFFICIAL_NOTICES_URL


def _year_windows(year: int) -> list[date]:
    return [date(year, month, day) for month, day in _WINDOWS_BY_YEAR.get(year, ())]


def _next_known_window(today: date) -> date | None:
    candidates = [window for year in (today.year, today.year + 1)
                  for window in _year_windows(year) if window >= today]
    return min(candidates) if candidates else None


@retry(max_attempts=2, base_delay=0.8)
def _fetch_json(url: str) -> Any:
    response = requests.get(
        url,
        headers={"User-Agent": _CALENDAR_USER_AGENT, "Accept": "application/json"},
        timeout=(8, 15),
    )
    response.raise_for_status()
    if len(response.content) > _MAX_CALENDAR_BYTES:
        raise ValueError("calendar response too large")
    return response.json()


def _parse_holiday_overrides(payload: Any, year: int) -> dict[date, bool]:
    """把 holiday-cn / Timor 两种结构统一为 ``日期 -> 是否工作日``。"""
    overrides: dict[date, bool] = {}
    if isinstance(payload, dict) and payload.get("year") == year:
        for item in payload.get("days", []):
            if not isinstance(item, dict) or not isinstance(item.get("isOffDay"), bool):
                continue
            try:
                day = date.fromisoformat(str(item.get("date", "")))
            except ValueError:
                continue
            if day.year == year:
                overrides[day] = not item["isOffDay"]
    elif isinstance(payload, dict) and payload.get("code") == 0:
        for item in (payload.get("holiday") or {}).values():
            if not isinstance(item, dict) or not isinstance(item.get("holiday"), bool):
                continue
            try:
                day = date.fromisoformat(str(item.get("date", "")))
            except ValueError:
                continue
            if day.year == year:
                overrides[day] = not item["holiday"]
    return overrides


def _fetch_holiday_overrides(year: int) -> dict[date, bool]:
    """国务院节假日安排的多镜像读取；全部失败时由调用方退回普通周一至周五。"""
    errors: list[str] = []
    for template in _CALENDAR_URLS:
        url = template.format(year=year)
        try:
            overrides = _parse_holiday_overrides(_fetch_json(url), year)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{type(exc).__name__}: {redact_secrets(str(exc))[:80]}")
            continue
        if overrides:
            logger.info(
                "jiangsu_fuel.calendar_loaded year=%d source=%s overrides=%d",
                year, urllib.parse.urlparse(url).netloc, len(overrides),
            )
            return overrides
        errors.append("empty calendar")
    logger.warning(
        "jiangsu_fuel.calendar_all_sources_failed year=%d errors=%s; using weekdays",
        year, " | ".join(errors)[:320],
    )
    return {}


def _is_workday(day: date, calendars: dict[int, dict[date, bool]]) -> bool:
    if day.year not in calendars:
        calendars[day.year] = _fetch_holiday_overrides(day.year)
    return calendars[day.year].get(day, day.weekday() < 5)


def _advance_ten_workdays(
    start: date,
    calendars: dict[int, dict[date, bool]],
) -> date:
    cursor = start
    count = 0
    while count < 10:
        cursor += timedelta(days=1)
        if _is_workday(cursor, calendars):
            count += 1
    return cursor


def _next_calculated_window(today: date) -> date | None:
    """从已核验的 2026-12-24 窗口向后滚动，每 10 个中国工作日生成窗口。"""
    anchor_windows = _year_windows(2026)
    if not anchor_windows:
        return None
    cursor = anchor_windows[-1]
    calendars: dict[int, dict[date, bool]] = {}
    # 防御异常远期输入，避免意外无限循环；100 年远超本项目实际生命周期。
    for _ in range(2600):
        cursor = _advance_ten_workdays(cursor, calendars)
        if cursor >= today:
            return cursor
    return None


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


def _amount_value(amount: str | float) -> float:
    if isinstance(amount, str):
        amount = amount.replace(",", "").replace("，", "")
    return float(amount)


def _signed_amount(direction: str, amount: str | float, unit: str) -> str:
    value = _amount_value(amount)
    rounded = round(value, 2)
    rendered = f"{rounded:.2f}".rstrip("0").rstrip(".")
    # 极小金额换算后可能四舍五入为零；此时不显示误导性的 +0 / -0。
    sign = "" if rounded == 0 else ("-" if direction == "下调" else "+")
    return f"{sign}{rendered} 元/{unit}"


def _ton_amount_as_per_liter(direction: str, amount: str | float) -> str:
    """把元/吨按汽油、柴油常用近似密度换算为人民币元/升。"""
    amount_per_ton = _amount_value(amount)
    gasoline = amount_per_ton * _GASOLINE_KG_PER_LITER / 1000
    diesel = amount_per_ton * _DIESEL_KG_PER_LITER / 1000
    return (
        f"汽油约 {_signed_amount(direction, gasoline, '升')}；"
        f"柴油约 {_signed_amount(direction, diesel, '升')}"
    )


def _detail_from_text(text: str, direction: str) -> str:
    if direction == "搁浅":
        return "预计不作调整"

    details: list[str] = []
    # 必须有明确的单位：不能将“92号汽油上调200元/吨”读成200元/升。
    pattern = re.compile(
        r"(上调|下调|上涨|下跌|提高|降低|涨|跌)"
        r"(?P<prefix>[^\d，,。；;]{0,12}?)"
        rf"(?P<amount>{_AMOUNT_RE})"
        rf"(?:\s*[-—~～至到]\s*(?P<upper>{_AMOUNT_RE}))?"
        r"\s*(?P<unit>元\s*[/／每]\s*[升吨]|元|分)"
    )
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    # 不把同方向的上一轮涨价金额挪到本轮。逗号分句但保留千位分隔符。
    clauses = re.split(r"[。；;\n]|[,，](?!\d{3}(?:\D|$))", text)
    for clause in clauses:
        current = re.search(r"本轮|本次|下轮|下次|新一轮", clause)
        if current:
            clause = clause[current.start():]
        elif re.search(r"上轮|上一轮|上次|此前|刚涨|刚跌", clause):
            continue
        for match in pattern.finditer(clause):
            item_direction = "下调" if match.group(1) in _LOWER_WORDS else "上调"
            if item_direction != direction:
                continue
            before = clause[:match.start()]
            unit = re.sub(r"\s", "", match.group("unit"))
            per_ton = "吨" in unit or "每吨" in before[-6:] + match.group("prefix")
            per_liter = "升" in unit or "每升" in before[-6:] + match.group("prefix")
            if not per_ton and not per_liter:
                continue
            amounts = [_amount_value(match.group("amount"))]
            if match.group("upper"):
                amounts.append(_amount_value(match.group("upper")))
            if not all(math.isfinite(value) and value >= 0 for value in amounts):
                continue
            grade = re.search(r"(?<!\d)(89|92|95|98|0)\s*[号#](?:汽油|柴油)?[^\d]{0,8}$", before)

            def render_values(multiplier: float, values: list[float] = amounts) -> str:
                return " ～ ".join(
                    _signed_amount(direction, value * multiplier, "升") for value in values
                )

            if per_ton:
                # 明确分开的汽油、柴油吨价各自换算，不复制同一个幅度。
                fuel = re.search(r"(汽油|柴油)[^\d]{0,8}$", before)
                labels = [fuel.group(1)] if fuel and "汽柴油" not in before else ["汽油", "柴油"]
                for label in labels:
                    density = _GASOLINE_KG_PER_LITER if label == "汽油" else _DIESEL_KG_PER_LITER
                    details.append(f"{label}约 {render_values(density / 1000)}")
            else:
                label = (f"{grade.group(1)} 号" if grade.group(1) != "0" else "0 号柴油") if grade else "汽柴油"
                details.append(f"{label}约 {render_values(0.01 if unit == '分' else 1)}")

    if details:
        return "；".join(dict.fromkeys(details))
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


def _candidate_score(candidate: ForecastCandidate, target: date) -> tuple[int, int, bool, int, float]:
    source_score = _TRUSTED_SOURCES.get(candidate.source, 0)
    date_score = 12 if candidate.mentions_target_date else 0
    # 调价日前后新闻很多。只取调价前发布的预测，避免误用当天正式结果或旧轮次。
    before_target = candidate.published_at.date() <= target
    timing_score = 3 if before_target else -20
    # 相同窗口、同日预测优先包含金额的完整信息；不能为凑金额倒退到旧日预测。
    return (
        date_score + timing_score,
        candidate.published_at.date().toordinal(),
        "元/升" in candidate.detail or candidate.direction == "搁浅",
        source_score,
        candidate.published_at.timestamp(),
    )


@retry(max_attempts=2, base_delay=1.0)
def _fetch_google_news_query(query: str) -> list[object]:
    encoded = urllib.parse.quote(query)
    url = (
        f"https://news.google.com/rss/search?q={encoded}"
        "&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
    )
    return list(fetch_rss(url).entries or [])


def _fetch_forecast_entries(target: date) -> list[object]:
    """用互补查询找预测；任一查询成功即可，不让单个 query 故障拖垮整组。"""
    date_text = f"{target.month}月{target.day}日"
    queries = (
        f"{date_text} 国内成品油 调价 预计 when:7d",
        f"{date_text} 成品油 原油变化率 调价窗口 when:7d",
        f"site:oilchem.net {date_text} 成品油 调价 when:14d",
        f"site:sci99.com {date_text} 成品油 调价 when:14d",
    )
    entries: list[object] = []
    successes = 0
    for query in queries:
        try:
            entries.extend(_fetch_google_news_query(query))
            successes += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "jiangsu_fuel.forecast_query_failed query=%r exc_type=%s msg=%s",
                query, type(exc).__name__, redact_secrets(str(exc))[:120],
            )
    if successes == 0:
        raise RuntimeError("all forecast queries failed")

    deduped: list[object] = []
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        key = (
            str(getattr(entry, "title", "") or "").strip(),
            str(getattr(entry, "link", "") or "").strip(),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(entry)
    return deduped


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


@retry(max_attempts=2, base_delay=1.0)
def _fetch_crude_history(symbol: str):
    history = yf.Ticker(symbol).history(period="3mo", interval="1d", auto_adjust=False)
    if history is None or history.empty or "Close" not in history:
        raise RuntimeError(f"empty crude history for {symbol}")
    return history


def _fetch_fred_crude_closes(
    series_id: str,
    *,
    api_key: str,
) -> list[float]:
    """FRED 官方日度现货价，作为 Yahoo/yfinance 的独立备源。"""
    resp = requests.get(
        "https://api.stlouisfed.org/fred/series/observations",
        params={
            "series_id": series_id,
            "api_key": api_key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": 60,
        },
        timeout=(8, 15),
    )
    # 不用 raise_for_status：HTTPError 的 URL 会把 api_key 写进 retry 日志。
    if resp.status_code != 200:
        raise RuntimeError(f"FRED {series_id} HTTP {resp.status_code}")
    observations = (resp.json() or {}).get("observations") or []
    closes: list[float] = []
    for item in reversed(observations):
        try:
            value = float(item.get("value"))
        except (TypeError, ValueError):
            continue
        if math.isfinite(value) and value > 0:
            closes.append(value)
    if len(closes) < 20:
        raise RuntimeError(f"FRED {series_id} 有效观测仅 {len(closes)} 条")
    return closes


def _ten_day_change(closes: list[float]) -> float | None:
    if len(closes) < 20:
        return None
    previous = sum(closes[-20:-10]) / 10
    current = sum(closes[-10:]) / 10
    return (current - previous) / previous


def _estimate_direction_from_crude(
    today: date,
    fred_api_key: str = "",
) -> tuple[str, float] | None:
    """用 Brent/WTI 最近两组 10 日均价估算方向。

    Yahoo/yfinance 两个期货符号均失败时，切换到 FRED 的
    Brent/WTI 官方日度现货序列。
    """
    changes: list[float] = []
    for symbol in _CRUDE_PROXY_SYMBOLS:
        try:
            history = _fetch_crude_history(symbol)
            closes = [
                float(value)
                for index, value in history["Close"].items()
                if index.date() <= today and math.isfinite(float(value)) and float(value) > 0
            ]
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "jiangsu_fuel.crude_proxy_failed symbol=%s exc_type=%s msg=%s",
                symbol, type(exc).__name__, redact_secrets(str(exc))[:120],
            )
            continue
        if (change := _ten_day_change(closes)) is not None:
            changes.append(change)

    if not changes and fred_api_key:
        for series_id in _FRED_CRUDE_SERIES:
            try:
                closes = _fetch_fred_crude_closes(series_id, api_key=fred_api_key)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "jiangsu_fuel.fred_crude_failed series=%s exc_type=%s msg=%s",
                    series_id,
                    type(exc).__name__,
                    redact_secrets(str(exc))[:120],
                )
                continue
            if (change := _ten_day_change(closes)) is not None:
                changes.append(change)
        if changes:
            logger.warning(
                "jiangsu_fuel.crude_fallback_used primary=yfinance fallback=fred series_count=%d",
                len(changes),
            )

    if not changes:
        return None
    average_change = sum(changes) / len(changes)
    if average_change > _CRUDE_DIRECTION_THRESHOLD:
        return "上调", average_change
    if average_change < -_CRUDE_DIRECTION_THRESHOLD:
        return "下调", average_change
    return "待定", average_change


def _is_alert_delivery_day(today: date, target: date) -> bool:
    days_until = (target - today).days
    if days_until in (1, 2):
        return True
    # 本晨报只在周二至周六发刊。周二调价时，前 1—2 天为周日、周一，
    # 因此在最近的可用发刊日周六提前一次，避免整个预告窗口被跳过。
    return days_until == 3 and target.weekday() == 1 and today.weekday() == 5


def fetch(*, today: date, fred_api_key: str = "") -> JiangsuFuelAlert | None:
    """在调价窗口前返回预告；任何预测源故障都不会让应显示的模块消失。"""
    target = (
        _next_known_window(today)
        or _discover_next_window(today)
        or _next_calculated_window(today)
    )
    if target is None:
        logger.info("jiangsu_fuel.no_upcoming_window today=%s", today.isoformat())
        return None

    days_until = (target - today).days
    if not _is_alert_delivery_day(today, target):
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
            and (target - timedelta(days=7)) <= candidate.published_at.date() <= today
        ]
        best = max(candidates, key=lambda item: _candidate_score(item, target), default=None)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "jiangsu_fuel.forecast_failed target=%s exc_type=%s msg=%s",
            target.isoformat(), type(exc).__name__, redact_secrets(str(exc))[:160],
        )
        best = None

    if best is None:
        crude_estimate = (
            _estimate_direction_from_crude(today, fred_api_key)
            if fred_api_key
            else _estimate_direction_from_crude(today)
        )
        if crude_estimate is not None and crude_estimate[0] != "待定":
            direction, change = crude_estimate
            logger.info(
                "jiangsu_fuel.crude_proxy target=%s direction=%s change=%.4f",
                target.isoformat(), direction, change,
            )
            return JiangsuFuelAlert(
                adjustment_date=target,
                days_until=days_until,
                direction=direction,
                detail=f"预计{direction}，具体幅度待更新",
                forecast_source="Brent/WTI 原油均价代理",
                forecast_method="crude_proxy",
            )

        logger.warning("jiangsu_fuel.schedule_only target=%s", target.isoformat())
        return JiangsuFuelAlert(
            adjustment_date=target,
            days_until=days_until,
            direction="待定",
            detail="涨跌方向与幅度待更新",
            forecast_method="schedule_only",
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
        forecast_method="news",
    )
