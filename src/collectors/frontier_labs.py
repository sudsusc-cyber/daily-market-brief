"""
Frontier Labs collector.

V1 monitors only OpenAI and Anthropic as a small fact layer for the daily brief.
They are not holdings; this collector keeps its own 7-day pushed state.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import urllib.parse
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from src.utils.dates import last_24h_window, to_beijing
from src.utils.fetch_rss import fetch_rss
from src.utils.retry import retry

logger = logging.getLogger(__name__)


SourceType = Literal["official", "google_news"]


@dataclass(frozen=True)
class FrontierLab:
    name: str
    queries: list[str]
    official_feeds: list[str]
    related_tickers: list[str]


@dataclass
class FrontierItem:
    lab: str
    title: str
    snippet: str
    published_at: datetime
    url: str
    source: str
    source_type: SourceType
    related_tickers: list[str]


@dataclass
class FrontierBundle:
    lab: str
    related_tickers: list[str]
    items: list[FrontierItem] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


FRONTIER_LABS: list[FrontierLab] = [
    FrontierLab(
        name="OpenAI",
        queries=['"OpenAI"'],
        official_feeds=["https://openai.com/news/rss.xml"],
        related_tickers=["MSFT", "GOOG", "NVDA", "TSM", "AMD"],
    ),
    FrontierLab(
        name="Anthropic",
        queries=['"Anthropic"'],
        official_feeds=[],
        related_tickers=["MSFT", "GOOG", "NVDA", "TSM", "AMD"],
    ),
]


def _entry_datetime(entry) -> datetime | None:  # noqa: ANN001
    pp = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if not pp:
        return None
    return datetime(*pp[:6], tzinfo=UTC)


def _source_title(entry, default: str) -> str:  # noqa: ANN001
    source = getattr(entry, "source", None)
    if source is not None:
        title = getattr(source, "title", None)
        if title:
            return str(title).strip()
        if isinstance(source, dict) and source.get("title"):
            return str(source["title"]).strip()
    return default


@retry(max_attempts=3, base_delay=1.5)
def _fetch_official_feed(feed_url: str, lab: FrontierLab) -> list[FrontierItem]:
    feed = fetch_rss(feed_url)
    feed_meta = getattr(feed, "feed", {}) or {}
    default_source = str(getattr(feed_meta, "title", "") or feed_meta.get("title", "") or lab.name)
    items: list[FrontierItem] = []
    for entry in feed.entries or []:
        pub = _entry_datetime(entry)
        if pub is None:
            continue
        title = str(getattr(entry, "title", "") or "").strip()
        if not title:
            continue
        items.append(
            FrontierItem(
                lab=lab.name,
                title=title,
                snippet=str(getattr(entry, "summary", "") or "").strip(),
                published_at=pub,
                url=str(getattr(entry, "link", "") or "").strip(),
                source=_source_title(entry, default_source),
                source_type="official",
                related_tickers=list(lab.related_tickers),
            )
        )
    return items


@retry(max_attempts=3, base_delay=1.5)
def _fetch_google_news(query: str, lab: FrontierLab) -> list[FrontierItem]:
    q = urllib.parse.quote(query)
    url = f"https://news.google.com/rss/search?q={q}+when:1d&hl=en-US&gl=US&ceid=US:en"
    feed = fetch_rss(url)
    items: list[FrontierItem] = []
    for entry in feed.entries or []:
        pub = _entry_datetime(entry)
        if pub is None:
            continue
        title = str(getattr(entry, "title", "") or "").strip()
        if not title:
            continue
        items.append(
            FrontierItem(
                lab=lab.name,
                title=title,
                snippet=str(getattr(entry, "summary", "") or "").strip(),
                published_at=pub,
                url=str(getattr(entry, "link", "") or "").strip(),
                source=_source_title(entry, "Google News"),
                source_type="google_news",
                related_tickers=list(lab.related_tickers),
            )
        )
    return items


def _normalize_title(title: str) -> str:
    title = (title or "").lower()
    title = re.sub(r"\s*[-—–]\s*[^-—–]+$", "", title).strip()
    title = re.sub(r"[^\w一-鿿]+", "", title, flags=re.UNICODE)
    return title[:100]


def _normalize_url(url: str) -> str:
    return (url or "").strip().lower()


def _content_hash(lab: str, item: FrontierItem) -> str:
    key = _normalize_title(item.title) or _normalize_url(item.url)
    h = hashlib.sha1(f"{lab}|{key}".encode()).hexdigest()
    return h[:16]


def _load_pushed(state_path: Path) -> dict[str, str]:
    if not state_path.exists():
        return {}
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("pushed_frontier_labs.parse_failed exc=%s; treating as empty", exc)
        return {}
    if not isinstance(data, dict):
        logger.warning(
            "pushed_frontier_labs.invalid_type type=%s; treating as empty",
            type(data).__name__,
        )
        return {}
    return {str(key): str(value) for key, value in data.items()}


def _save_pushed(state_path: Path, data: dict[str, str]) -> None:
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = state_path.with_suffix(state_path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        tmp.replace(state_path)
    except OSError as exc:
        logger.warning(
            "pushed_frontier_labs.save_failed exc=%s; 7-day dedupe disabled this run",
            exc,
        )


def _purge_expired(pushed: dict[str, str], now: datetime, days: int = 7) -> dict[str, str]:
    cutoff = now - timedelta(days=days)
    out: dict[str, str] = {}
    for key, value in pushed.items():
        try:
            ts = datetime.fromisoformat(value)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
        except (TypeError, ValueError):
            continue
        if ts >= cutoff:
            out[key] = value
    return out


def _dedupe_items(items: list[FrontierItem]) -> list[FrontierItem]:
    seen_titles: set[str] = set()
    seen_urls: set[str] = set()
    kept: list[FrontierItem] = []
    for item in items:
        title_key = _normalize_title(item.title)
        url_key = _normalize_url(item.url)
        if title_key and title_key in seen_titles:
            continue
        if url_key and url_key in seen_urls:
            continue
        if title_key:
            seen_titles.add(title_key)
        if url_key:
            seen_urls.add(url_key)
        kept.append(item)
    return kept


def fetch_all(
    state_path: Path,
    *,
    max_items_per_lab: int = 8,
) -> tuple[list[FrontierBundle], dict[str, str]]:
    """Fetch last-24h OpenAI/Anthropic candidates.

    Returns (bundles, pending_pushed). The caller should commit pending_pushed
    only after the email has been sent successfully.
    """
    start_utc, end_utc = last_24h_window()
    pushed = _purge_expired(_load_pushed(state_path), end_utc)
    pending_pushed = dict(pushed)

    bundles: list[FrontierBundle] = []
    for lab in FRONTIER_LABS:
        raw: list[FrontierItem] = []
        errors: list[str] = []

        for feed_url in lab.official_feeds:
            try:
                raw.extend(_fetch_official_feed(feed_url, lab))
            except Exception as exc:  # noqa: BLE001
                msg = f"official {type(exc).__name__}: {exc}"
                errors.append(msg)
                logger.warning(
                    "frontier_labs.official_fetch_failed lab=%s url=%s exc=%s",
                    lab.name,
                    feed_url,
                    exc,
                )

        for query in lab.queries:
            try:
                raw.extend(_fetch_google_news(query, lab))
            except Exception as exc:  # noqa: BLE001
                msg = f"google_news {type(exc).__name__}: {exc}"
                errors.append(msg)
                logger.warning(
                    "frontier_labs.google_news_fetch_failed lab=%s query=%s exc=%s",
                    lab.name,
                    query,
                    exc,
                )

        in_window = [
            item
            for item in raw
            if start_utc <= item.published_at < end_utc and _content_hash(lab.name, item) not in pushed
        ]
        deduped = _dedupe_items(in_window)
        deduped.sort(key=lambda item: (item.published_at, item.source_type == "official"), reverse=True)
        limited = deduped[:max_items_per_lab]
        for item in limited:
            pending_pushed[_content_hash(lab.name, item)] = end_utc.isoformat()

        bundles.append(
            FrontierBundle(
                lab=lab.name,
                related_tickers=list(lab.related_tickers),
                items=limited,
                errors=errors,
            )
        )
        logger.info(
            "frontier_labs lab=%s total=%d kept=%d errors=%d",
            lab.name,
            len(raw),
            len(limited),
            len(errors),
        )

    return bundles, pending_pushed


def commit_pushed(state_path: Path, pending_pushed: dict[str, str]) -> None:
    _save_pushed(state_path, pending_pushed)


def format_published_beijing(item: FrontierItem) -> str:
    return to_beijing(item.published_at).strftime("%m-%d %H:%M")
