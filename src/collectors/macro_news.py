"""
宏观重大新闻采集(模块 4)。

数据源:WSJ / FT / Bloomberg(proxy)RSS。
M1 验证三源全部可达;ADR-0001 §4 标注 Bloomberg 是非官方代理需观察,
M3 加备选源 Reuters Top News 作为兜底,主源全失败时也至少有一个字典里有内容。

时间窗口:过去 24 小时(滚动窗口,不是"昨日整日")。
理由:宏观新闻"今早 6:00 发布的、用户还没看到"的也应纳入,用日历窗口会漏。

输出:每个源一份 MacroFeedBundle,含若干 MacroNewsItem。
M3 只把头条标题列表 dump 到模板;M4 起 LLM 会做"头版级别"筛选 + 段落叙述。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from src.utils.dates import last_24h_window, to_beijing
from src.utils.fetch_rss import fetch_rss
from src.utils.retry import retry

logger = logging.getLogger(__name__)


@dataclass
class MacroNewsItem:
    title: str
    published_at: datetime  # aware UTC
    url: str
    source: str  # "WSJ" / "FT" / "Bloomberg" / "Reuters"


@dataclass
class MacroFeedBundle:
    source: str
    items: list[MacroNewsItem] = field(default_factory=list)
    error: str | None = None


_FEEDS: dict[str, str] = {
    "WSJ": "https://feeds.content.dowjones.io/public/rss/RSSWorldNews",
    "FT": "https://www.ft.com/?format=rss",
    "Bloomberg": "https://feeds.bloomberg.com/markets/news.rss",
    # Reuters 自 2020 年起逐步关停公开 RSS,改用 CNBC Top News(财经向更贴本项目主题)
    "CNBC": "https://www.cnbc.com/id/100003114/device/rss/rss.html",
}


@retry(max_attempts=3, base_delay=1.5)
def _fetch_feed(url: str) -> list[MacroNewsItem]:
    feed = fetch_rss(url)
    items: list[MacroNewsItem] = []
    for e in feed.entries or []:
        pp = getattr(e, "published_parsed", None) or getattr(e, "updated_parsed", None)
        if not pp:
            continue
        pub = datetime(*pp[:6], tzinfo=UTC)
        items.append(MacroNewsItem(
            title=str(getattr(e, "title", "") or "").strip(),
            published_at=pub,
            url=str(getattr(e, "link", "") or ""),
            source="",  # 由调用方填充
        ))
    return items


def fetch_all() -> list[MacroFeedBundle]:
    """采集所有宏观源,返回每源 24h 内条目列表(失败的源 error 字段填原因)"""
    start_utc, end_utc = last_24h_window()
    bundles: list[MacroFeedBundle] = []
    for name, url in _FEEDS.items():
        try:
            raw = _fetch_feed(url)
        except Exception as exc:  # noqa: BLE001
            logger.exception("macro_news.fetch_failed source=%s", name)
            bundles.append(MacroFeedBundle(
                source=name, error=f"{type(exc).__name__}: {exc}",
            ))
            continue
        for item in raw:
            item.source = name
        recent = [it for it in raw if start_utc <= it.published_at < end_utc]
        recent.sort(key=lambda x: x.published_at, reverse=True)
        bundles.append(MacroFeedBundle(source=name, items=recent))
        logger.info("macro_news source=%s total=%d 24h=%d", name, len(raw), len(recent))
    return bundles


def format_published_beijing(item: MacroNewsItem) -> str:
    return to_beijing(item.published_at).strftime("%m-%d %H:%M")
