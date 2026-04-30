"""
关键人物发言采集(模块 3a/3b)。

监控对象:
  - 黄仁勋 Jensen Huang(NVDA CEO,持仓相关性高)
  - 巴菲特 Warren Buffett(投资框架核心人物)

数据源:Google News RSS,过去 24 小时。
处理(M3 阶段):
  1. 拉所有结果
  2. **第一道筛选**(规则):标题或摘要包含 "Said/Says/Tells/Told/Announced/Speaks"
     等动词,过滤"分析师评论"型噪音。LLM 第二道筛选(是否本人原话)留给 M4。
  3. 去重:7 天内已推送过的 (person, content_hash) 不再出现
  4. 状态持久化:state/pushed_figures.json

输出:每个人物一份 FigureBundle,含若干 FigureMention。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.utils.dates import last_24h_window, to_beijing
from src.utils.fetch_rss import fetch_rss
from src.utils.retry import retry

logger = logging.getLogger(__name__)


@dataclass
class FigureMention:
    title: str
    snippet: str
    published_at: datetime
    url: str
    source: str  # 媒体名


@dataclass
class FigureBundle:
    person: str  # 显示名,中文或英文
    query: str   # Google News 搜索 query
    items: list[FigureMention] = field(default_factory=list)
    error: str | None = None


# 监控人物清单。第三个字段是 Google News 语言:"en" 走英文搜索,"zh" 走中文。
# 李录每日候选过少(24h 通常 0-2 条),已弃用——若以后频率上升可加回。
FIGURES: list[tuple[str, str, str]] = [
    ("黄仁勋", '"Jensen Huang"', "en"),
    ("巴菲特", '"Warren Buffett"', "en"),
    ("但斌", '"但斌"', "zh"),    # 东方港湾董事长,中文价值投资圈
]

# 规则筛选动词:英文 + 中文双语,任一命中即视为候选
_VERB_RE = re.compile(
    # 英文
    r"\b(?:said|says|tells|told|announce[ds]?|speaks?|spoke|warns?|"
    r"expects?|expected|believes?|noted|comment(?:ed|s)?|interview)\b"
    # 中文(动词或表态词,不要太宽)
    r"|(?:说|表示|称|认为|发表|讲到|提到|强调|指出|警告|建议|相信|预计|"
    r"分析|评论|看法|观点|采访|演讲|致辞|呼吁|批评|回应)",
    re.IGNORECASE,
)


@retry(max_attempts=3, base_delay=1.5)
def _fetch_google_news(query: str, lang: str = "en") -> list[FigureMention]:
    q = urllib.parse.quote(query)
    if lang == "zh":
        url = f"https://news.google.com/rss/search?q={q}+when:1d&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
    else:
        url = f"https://news.google.com/rss/search?q={q}+when:1d&hl=en-US&gl=US&ceid=US:en"
    feed = fetch_rss(url)
    items: list[FigureMention] = []
    for e in feed.entries or []:
        pp = getattr(e, "published_parsed", None) or getattr(e, "updated_parsed", None)
        if not pp:
            continue
        pub = datetime(*pp[:6], tzinfo=timezone.utc)
        title = str(getattr(e, "title", "") or "").strip()
        snippet = str(getattr(e, "summary", "") or "").strip()
        items.append(FigureMention(
            title=title,
            snippet=snippet,
            published_at=pub,
            url=str(getattr(e, "link", "") or ""),
            source=str(getattr(getattr(e, "source", None), "title", "") or "Google News"),
        ))
    return items


def _content_hash(person: str, item: FigureMention) -> str:
    h = hashlib.sha1(f"{person}|{item.title}".encode("utf-8")).hexdigest()
    return h[:16]


def _passes_first_filter(item: FigureMention) -> bool:
    """第一道规则筛选:标题 / 摘要必须包含动词类关键词"""
    text = f"{item.title}\n{item.snippet}"
    return bool(_VERB_RE.search(text))


# ---------- 状态持久化(7 天去重) ----------
def _load_pushed(state_path: Path) -> dict[str, str]:
    """{content_hash: ISO8601 推送时间}"""
    if not state_path.exists():
        return {}
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("pushed_figures.parse_failed exc=%s; treating as empty", exc)
        return {}


def _save_pushed(state_path: Path, data: dict[str, str]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _purge_expired(pushed: dict[str, str], now: datetime, days: int = 7) -> dict[str, str]:
    cutoff = now - timedelta(days=days)
    out = {}
    for k, v in pushed.items():
        try:
            ts = datetime.fromisoformat(v)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if ts >= cutoff:
            out[k] = v
    return out


# ---------- 入口 ----------
def fetch_all(state_path: Path) -> list[FigureBundle]:
    """采集所有监控人物的过去 24h 候选发言,完成第一道筛选 + 7 天去重"""
    start_utc, end_utc = last_24h_window()

    pushed = _purge_expired(_load_pushed(state_path), end_utc)
    new_pushed = dict(pushed)  # 本次新加入的也写入

    bundles: list[FigureBundle] = []
    for person, query, lang in FIGURES:
        try:
            raw = _fetch_google_news(query, lang=lang)
        except Exception as exc:  # noqa: BLE001
            logger.exception("figures.fetch_failed person=%s", person)
            bundles.append(FigureBundle(
                person=person, query=query,
                error=f"{type(exc).__name__}: {exc}",
            ))
            continue
        kept: list[FigureMention] = []
        for item in raw:
            if not (start_utc <= item.published_at < end_utc):
                continue
            if not _passes_first_filter(item):
                continue
            h = _content_hash(person, item)
            if h in pushed:
                continue
            kept.append(item)
            new_pushed[h] = end_utc.isoformat()
        kept.sort(key=lambda x: x.published_at, reverse=True)
        bundles.append(FigureBundle(person=person, query=query, items=kept))
        logger.info("figures person=%s total=%d kept=%d", person, len(raw), len(kept))

    _save_pushed(state_path, new_pushed)
    return bundles


def format_published_beijing(item: FigureMention) -> str:
    return to_beijing(item.published_at).strftime("%m-%d %H:%M")
