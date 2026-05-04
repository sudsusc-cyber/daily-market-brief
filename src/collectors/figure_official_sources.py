"""
关键人物官方源补充采集（非替代 Google News）。

只接稳定、简单、可解析的 RSS / HTML 源。官方源命中后仍须经过现有规则过滤、
DeepSeek 质量评分、跨媒体合并和版面限流，不因为是官方源就自动展示。

输出复用 FigureMention，合并进 figures.py 的 FigureBundle。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

import feedparser
import requests
from bs4 import BeautifulSoup

from src.collectors.figures import FigureMention

logger = logging.getLogger(__name__)

# ── 官方源配置 ──


@dataclass
class OfficialSource:
    source_name: str       # 展示用，如 "OpenAI" / "Microsoft Blog"
    url: str               # RSS 或 HTML 地址
    aliases: dict[str, list[str]] = field(default_factory=dict)
    # aliases: {person_display_name: [name_variant, ...]}
    # 每条 entry 的 title+summary 必须命中至少一个 variant 才归入该人物
    parser_type: str = "rss"  # "rss" 或 "html"
    authority_rank: int = 0   # 0 = 最高权威


OFFICIAL_SOURCES: list[OfficialSource] = [
    OfficialSource(
        source_name="OpenAI",
        url="https://openai.com/news/rss.xml",
        aliases={"奥特曼": ["Sam Altman", "Altman", "奥特曼"]},
        parser_type="rss",
        authority_rank=0,
    ),
    OfficialSource(
        source_name="Microsoft Blog",
        url="https://blogs.microsoft.com/feed/",
        aliases={"纳德拉": ["Satya Nadella", "Nadella", "纳德拉"]},
        parser_type="rss",
        authority_rank=0,
    ),
    OfficialSource(
        source_name="AMD IR",
        url="https://ir.amd.com/news-events/press-releases/rss",
        aliases={"苏妈": ["Lisa Su", "Dr. Su", "Dr Lisa Su", "苏妈"]},
        parser_type="rss",
        authority_rank=0,
    ),
    OfficialSource(
        source_name="Berkshire Hathaway",
        url="https://www.berkshirehathaway.com/news/{year}news.html",  # 运行时用当前年份填充
        aliases={
            "巴菲特": ["Warren Buffett", "Buffett", "巴菲特"],
            "阿贝尔": ["Greg Abel", "Abel", "阿贝尔"],
        },
        parser_type="html",
        authority_rank=0,
    ),
]


# ── 通用抓取工具 ──

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/16.6 Safari/605.1.15"
)

_REQUEST_TIMEOUT = 15


def _fetch_rss_entries(url: str) -> list[dict]:
    """抓取 RSS feed，返回条目列表。失败抛异常由调用方处理。"""
    resp = requests.get(
        url,
        headers={
            "User-Agent": _BROWSER_UA,
            "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8",
        },
        timeout=_REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    feed = feedparser.parse(resp.content)
    entries: list[dict] = []
    for e in feed.entries or []:
        entries.append({
            "title": getattr(e, "title", "") or "",
            "link": getattr(e, "link", "") or "",
            "summary": getattr(e, "summary", "") or "",
            "published_parsed": getattr(e, "published_parsed", None),
        })
    logger.debug("official_rss url=%s entries=%d", url, len(entries))
    return entries


def _fetch_berkshire_entries(url: str) -> list[dict]:
    """抓取 Berkshire 年度新闻 HTML 页面，提取链接列表。"""
    resp = requests.get(
        url,
        headers={"User-Agent": _BROWSER_UA},
        timeout=_REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    soup = BeautifulSoup(resp.content, "lxml")
    entries: list[dict] = []
    for a_tag in soup.find_all("a"):
        href = (a_tag.get("href") or "").strip()
        text = a_tag.get_text(strip=True)
        if not href or not text:
            continue
        if href.startswith("#"):
            continue
        # 补全相对 URL
        if href.startswith("/"):
            href = "https://www.berkshirehathaway.com" + href
        elif not href.startswith("http"):
            href = "https://www.berkshirehathaway.com/news/" + href
        entries.append({
            "title": text,
            "link": href,
            "summary": text,
            "published_parsed": None,  # Berkshire 页面无精确时间，交给日期解析
        })
    logger.debug("official_html url=%s entries=%d", url, len(entries))
    return entries


# ── 人物匹配 ──


def _person_matches(text: str, aliases: list[str]) -> bool:
    """text 中（大小写不敏感）命中任一 alias 即匹配。"""
    lower = text.lower()
    return any(a.lower() in lower for a in aliases)


# ── 日期解析 ──

# Berkshire URL 常见模式: news0426.html → 当年 04-26
_BRK_URL_DATE_RE = re.compile(r"news(\d{2})(\d{2})\.html?", re.IGNORECASE)
# 从 Berkshire 页面 URL 提取年份: /news/2026news.html → 2026
_BRK_PAGE_YEAR_RE = re.compile(r"/(\d{4})news\.html?")

# 通用 URL 日期: /2026/05/04/ 或 /2026-05-04-
_URL_DATE_RE = re.compile(r"/(\d{4})/(\d{2})/(\d{2})[/\-]")
_URL_DATE_ALT_RE = re.compile(r"/(\d{4})-(\d{2})-(\d{2})[-/]")


def _parse_entry_date(entry: dict, *, default_year: int | None = None) -> datetime | None:
    """从条目中解析发布时间。优先级:
    1. published_parsed (RSS 标准字段)
    2. URL 中的日期模式（/2026/05/04/ 或 2026-05-04）
    3. Berkshire 短链接模式（news0426.html），使用 default_year
    4. 解析不到返回 None（不兜底 datetime.now()）
    """
    pp = entry.get("published_parsed")
    if pp:
        try:
            return datetime(*pp[:6], tzinfo=UTC)
        except (TypeError, ValueError):
            pass

    # 尝试从 URL 解析
    url = entry.get("link", "")
    if url:
        m = _URL_DATE_RE.search(url) or _URL_DATE_ALT_RE.search(url)
        if m:
            try:
                return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=UTC)
            except ValueError:
                pass
        # Berkshire 特定模式: news0426.html → 使用 default_year
        m = _BRK_URL_DATE_RE.search(url)
        if m and default_year is not None:
            try:
                return datetime(default_year, int(m.group(1)), int(m.group(2)), tzinfo=UTC)
            except ValueError:
                pass

    return None


# ── 入口 ──


def _entry_to_mention(entry: dict, source_name: str) -> FigureMention:
    """将条目字典转为 FigureMention。调用方保证已通过日期检查。"""
    pp = entry.get("published_parsed")
    if pp:
        pub = datetime(*pp[:6], tzinfo=UTC)
    else:
        # 兜底：调用方应在传入前用 _parse_entry_date 过滤，此处不应到达
        pub = datetime.now(UTC)
    title = (entry.get("title") or "").strip()
    snippet = (entry.get("summary") or "").strip()
    if not snippet:
        snippet = title
    return FigureMention(
        title=title,
        snippet=snippet,
        published_at=pub,
        url=str(entry.get("link") or ""),
        source=source_name,
    )


def fetch_all(
    start_utc: datetime,
    end_utc: datetime,
) -> dict[str, list[FigureMention]]:
    """拉取所有官方源，返回 {person_display_name: [FigureMention, ...]}。

    每条 entry 必须:
      1. 标题+摘要命中该人物的 aliases
      2. 发布时间在 [start_utc, end_utc) 窗口内
      3. 发布日期可解析（无日期条目丢弃，不兜底 datetime.now()）

    每个源失败只记 warning，不阻断其他源。调用方（figures.py）负责
    对返回结果再执行 _passes_first_filter + pushed 去重。
    """
    result: dict[str, list[FigureMention]] = {}
    current_year = datetime.now(UTC).year

    for src in OFFICIAL_SOURCES:
        # 模板 URL（Berkshire {year}）用当前年份填充
        url = src.url.format(year=current_year)

        # 提取 Berkshire 页面年份，供短链接日期解析使用
        brk_year: int | None = None
        m = _BRK_PAGE_YEAR_RE.search(url)
        if m:
            brk_year = int(m.group(1))

        try:
            if src.parser_type == "rss":
                raw_entries = _fetch_rss_entries(url)
            else:
                raw_entries = _fetch_berkshire_entries(url)
        except Exception as exc:
            logger.warning(
                "official_src.failed source=%s url=%s exc=%s",
                src.source_name, url, exc,
            )
            continue

        matched_count = 0
        dated_count = 0
        window_count = 0

        for entry in raw_entries:
            text = f"{entry.get('title', '')} {entry.get('summary', '')}"

            # 逐人物匹配
            for person, aliases in src.aliases.items():
                if not _person_matches(text, aliases):
                    continue
                matched_count += 1

                # 日期过滤：无日期丢弃。Berkshire 短链接用页面年份解析
                pub = _parse_entry_date(entry, default_year=brk_year)
                if pub is None:
                    continue
                dated_count += 1

                # 时间窗口过滤
                if not (start_utc <= pub < end_utc):
                    continue
                window_count += 1

                mention = FigureMention(
                    title=(entry.get("title") or "").strip(),
                    snippet=(entry.get("summary") or "").strip() or (entry.get("title") or "").strip(),
                    published_at=pub,
                    url=str(entry.get("link") or ""),
                    source=src.source_name,
                )
                result.setdefault(person, []).append(mention)

        logger.info(
            "official_src.ok source=%s raw=%d matched=%d dated=%d window=%d people=%s",
            src.source_name,
            len(raw_entries),
            matched_count,
            dated_count,
            window_count,
            ",".join(src.aliases.keys()),
        )

    return result
