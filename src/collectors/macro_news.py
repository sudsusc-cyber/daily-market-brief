"""
宏观重大新闻采集(模块 4)。

数据源:WSJ / FT / Bloomberg / CNBC RSS。
M1 验证四源全部可达;ADR-0001 §4 标注 Bloomberg 是非官方代理需观察,
M3 加备选源 CNBC Top News 作为兜底,主源全失败时也至少有一个字典里有内容。

时间窗口:过去 24 小时(滚动窗口,不是"昨日整日")。
理由:宏观新闻"今早 6:00 发布的、用户还没看到"的也应纳入,用日历窗口会漏。

两层去重:
  1. 同日 SequenceMatcher 模糊去重(跨 source, threshold=0.72):
     WSJ / FT / Bloomberg 对同一事件的不同标题视为重复,
     按 source 优先级(Reuters > Bloomberg > FT > WSJ > CNBC)保留代表。
  2. 跨天 7 天 hash 去重:已推送过的 (source, 归一化标题 hash) 不再出现,
     状态持久化到 state/pushed_macro_news.json。

延后写盘——邮件发送成功后才 commit 跨天去重,失败时下次 run 仍能重新评估。

输出:每个源一份 MacroFeedBundle,含若干 MacroNewsItem。
M3 只把头条标题列表 dump 到模板;M4 起 LLM 会做"头版级别"筛选 + 段落叙述。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path

from src.utils.dates import last_24h_window, to_beijing
from src.utils.fetch_rss import fetch_rss
from src.utils.retry import retry
from src.utils.secrets import redact_secrets

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

# 跨 source 模糊去重时的来源优先级(数字越小优先级越高)
_SOURCE_PRIORITY: dict[str, int] = {
    "WSJ": 3,
    "FT": 2,
    "Bloomberg": 1,
    "CNBC": 4,
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


# ---------- 同日 SequenceMatcher 模糊去重(Phase 2) ----------
def _normalize_for_similarity(text: str) -> str:
    """归一化:去空白 + 去标点 + 小写。"""
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"[,。!?:;、—\-()()【】《》\"\"'']", "", text)
    return text.lower()


def _similar(a: str, b: str, threshold: float = 0.72) -> bool:
    """SequenceMatcher 相似度 ≥ threshold 视为同一事件。

    阈值 0.72,与 company_news 保持一致。
    """
    return SequenceMatcher(
        None, _normalize_for_similarity(a), _normalize_for_similarity(b),
    ).ratio() >= threshold


# ---------- 状态持久化(7 天跨天去重,Phase 1) ----------
def _content_hash(source: str, item: MacroNewsItem) -> str:
    """归一化 hash:去媒体后缀 / 去标点空白 / 截 80 字 / 加 source 前缀。

    source 作为前缀而非 ticker——macro 不绑公司,同一标题被不同媒体登
    不算同一 hash(各家是独立发布,各自 hash 没问题)。
    """
    title = (item.title or "").lower()
    title = re.sub(r"\s*[-—–]\s*[^-—–]+$", "", title).strip()
    title = re.sub(r"[^\w一-鿿]+", "", title, flags=re.UNICODE)
    title = title[:80]
    h = hashlib.sha1(f"{source}|{title}".encode()).hexdigest()
    return h[:16]


def _load_pushed_macro(state_path: Path) -> dict[str, str]:
    """{content_hash: ISO8601 推送时间}"""
    if not state_path.exists():
        return {}
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("pushed_macro_news.parse_failed exc=%s; treating as empty", exc)
        return {}


def _save_pushed_macro(state_path: Path, data: dict[str, str]) -> None:
    """原子写入 + 失败容错。"""
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = state_path.with_suffix(state_path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        tmp.replace(state_path)
    except OSError as exc:
        logger.warning("pushed_macro_news.save_failed exc=%s; 7 天去重本轮失效,主流程继续", exc)


def _purge_expired_macro(pushed: dict[str, str], now: datetime, days: int = 7) -> dict[str, str]:
    cutoff = now - timedelta(days=days)
    out: dict[str, str] = {}
    for k, v in pushed.items():
        try:
            ts = datetime.fromisoformat(v)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
        except (ValueError, TypeError):
            continue
        if ts >= cutoff:
            out[k] = v
    return out


def fetch_all(*, state_path: Path) -> tuple[list[MacroFeedBundle], dict[str, str]]:
    """采集所有宏观源 + 同日跨 source 模糊去重 + 7 天跨天 hash 去重。

    返回 (bundles, pending_pushed):
      - bundles:供下游 macro_filter / 渲染消费
      - pending_pushed:本次"应去重"的 hash → ISO time。**fetch_all 不写盘**;
        调用方在邮件成功发送后调 commit_pushed 提交。

    与 company_news 的关键差异:
      - _dedupe_fuzzy 是跨 source 的(WSJ/FT/Bloomberg 同事件应合并)
      - hash 前缀用 source 名而非 ticker
    """
    start_utc, end_utc = last_24h_window()
    pushed = _purge_expired_macro(_load_pushed_macro(state_path), end_utc)
    new_pushed = dict(pushed)

    # 1) 拉所有源
    bundles: list[MacroFeedBundle] = []
    for name, url in _FEEDS.items():
        try:
            raw = _fetch_feed(url)
        except Exception as exc:  # noqa: BLE001
            logger.error("macro_news.fetch_failed source=%s exc_type=%s msg=%s", name, type(exc).__name__, redact_secrets(str(exc))[:200])
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

    # 2) 同日跨 source 模糊去重
    #    按 source 优先级 + 发布时间排序(优先级高的源优先,同优先级按时间新旧)
    all_items: list[tuple[MacroNewsItem, MacroFeedBundle]] = []
    for b in bundles:
        if b.error or not b.items:
            continue
        for it in b.items:
            all_items.append((it, b))
    all_items.sort(
        key=lambda x: (_SOURCE_PRIORITY.get(x[0].source, 99), -x[0].published_at.timestamp()),
    )

    deduped: list[tuple[MacroNewsItem, MacroFeedBundle]] = []
    for it, b in all_items:
        if any(_similar(it.title, k.title) for k, _ in deduped):
            continue
        deduped.append((it, b))

    # 回填到对应 bundle
    fuzzy_kept: dict[int, list[MacroNewsItem]] = defaultdict(list)
    for it, b in deduped:
        fuzzy_kept[id(b)].append(it)
    for b in bundles:
        if b.error:
            continue
        before = len(b.items)
        kept_items = fuzzy_kept.get(id(b), [])
        kept_items.sort(key=lambda x: x.published_at, reverse=True)
        b.items = kept_items
        if len(b.items) < before:
            logger.info(
                "macro_news.fuzzy_dedup source=%s before=%d after=%d (同日跨 source 模糊去重)",
                b.source, before, len(b.items),
            )

    # 3) 跨天 hash 去重
    for b in bundles:
        if b.error or not b.items:
            continue
        kept: list[MacroNewsItem] = []
        for it in b.items:
            h = _content_hash(b.source, it)
            if h in pushed:
                continue
            kept.append(it)
            new_pushed[h] = end_utc.isoformat()
        if len(kept) < len(b.items):
            logger.info(
                "macro_news.dedup source=%s before=%d after=%d (跨 7 天去重)",
                b.source, len(b.items), len(kept),
            )
        b.items = kept

    return bundles, new_pushed


def commit_pushed(state_path: Path, pending_pushed: dict[str, str]) -> None:
    """邮件发送成功后调用 — 把 fetch_all 返回的 pending_pushed 落盘。"""
    _save_pushed_macro(state_path, pending_pushed)


def format_published_beijing(item: MacroNewsItem) -> str:
    return to_beijing(item.published_at).strftime("%m-%d %H:%M")
