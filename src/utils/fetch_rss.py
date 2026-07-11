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

_MAX_FEED_BYTES = 5 * 1024 * 1024

# 部分站点对 feedparser 默认 UA 直接 RST/403,这里用 Safari UA
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/16.6 Safari/605.1.15"
)


def fetch_rss(url: str, timeout: int = 20) -> Any:
    """
    用 requests 拉到字节,再交 feedparser 解析,返回 feedparser 的 FeedParserDict。

    raises requests.RequestException / feedparser 异常由调用方处理(通常套 retry / fallback)。

    timeout 拆 (connect, read):部分站点 TLS 握手秒级,但慢慢吐 chunked body 长达 60s+,
    单个 timeout=20 是 connect+read 合并(单次任意阶段超时即抛),把 read 限到 20 避免
    一个慢站把 @retry(3 次) 总耗 60s+ 拖到 daily.yml 的 15 分钟 job timeout。
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
        timeout=(min(10, timeout), timeout),
        stream=True,
    )
    try:
        resp.raise_for_status()
        content_type = (resp.headers.get("Content-Type") or "").lower()
        if "html" in content_type:
            raise ValueError(f"RSS endpoint returned HTML Content-Type: {content_type}")

        chunks: list[bytes] = []
        total = 0
        for chunk in resp.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > _MAX_FEED_BYTES:
                raise ValueError(f"RSS response exceeds {_MAX_FEED_BYTES} bytes")
            chunks.append(chunk)
    finally:
        resp.close()
    content = b"".join(chunks)
    head = content[:512].lstrip().lower()
    if head.startswith((b"<!doctype html", b"<html")):
        raise ValueError("RSS endpoint returned an HTML body")

    feed = feedparser.parse(content)
    if not getattr(feed, "version", "") and not (feed.entries or []):
        raise ValueError("response is not a recognizable RSS/Atom feed")
    logger.debug("fetch_rss url=%s entries=%d", url, len(feed.entries or []))
    return feed
