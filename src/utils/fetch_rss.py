"""
RSS 抓取工具。

从 M1 的 verify_sources.py 中提取(ADR-0001 §5):
  feedparser.parse(url) 直连许多站点(WSJ / FT / Bloomberg / Google News)会被拒,
  必须用 requests + 浏览器级 User-Agent 先拿到字节,再交 feedparser 解析。

用法:
    from src.utils.fetch_rss import fetch_rss
    feed = fetch_rss("https://www.ft.com/?format=rss")
    for entry in feed.entries:
        print(entry.title, entry.published)
"""

from __future__ import annotations

import logging
from typing import Any

import feedparser
import requests

logger = logging.getLogger(__name__)

# 部分站点对 feedparser 默认 UA 直接 RST/403,这里用 Safari UA
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/16.6 Safari/605.1.15"
)


def fetch_rss(url: str, timeout: int = 20) -> Any:
    """
    用 requests 拉到字节,再交 feedparser 解析,返回 feedparser 的 FeedParserDict。

    raises requests.RequestException / feedparser 异常由调用方处理(通常套 retry / fallback)。
    """
    resp = requests.get(
        url,
        headers={
            "User-Agent": _BROWSER_UA,
            "Accept": (
                "application/rss+xml, application/atom+xml, "
                "application/xml;q=0.9, */*;q=0.8"
            ),
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    feed = feedparser.parse(resp.content)
    logger.debug("fetch_rss url=%s entries=%d", url, len(feed.entries or []))
    return feed
