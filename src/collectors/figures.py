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
from datetime import UTC, datetime, timedelta
from pathlib import Path

import finnhub  # type: ignore[import-untyped]

from src.utils.dates import last_24h_window, to_beijing
from src.utils.fetch_rss import fetch_rss
from src.utils.retry import retry
from src.utils.secrets import redact_secrets

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
    person_en: str = ""  # 编辑式 byline 用的英文标准写法
    items: list[FigureMention] = field(default_factory=list)
    error: str | None = None


# 监控人物清单。第 1 列是 display name(中文优先,控制邮件署名与 7 天去重 hash),
# 第 3 列 lang:"en" 走英文搜索,"zh" 走中文。第 4 列是 byline 用英文名。
# 李录每日候选过少(24h 通常 0-2 条),已弃用——若以后频率上升可加回。
# 优先级说明：P0(巴菲特/阿贝尔/黄仁勋) > P1(苏妈等) > P2(奥特曼/但斌等)
FIGURES: list[tuple[str, str, str, str]] = [
    # P0
    ("黄仁勋",   '"Jensen Huang"',                       "en", "Jensen Huang"),
    ("巴菲特",   '"Warren Buffett"',                     "en", "Warren Buffett"),
    # P1
    ("苏妈",     '"Lisa Su" AMD',                        "en", "Lisa Su"),
    ("魏哲家",   '"C.C. Wei" OR "C. C. Wei" TSMC',      "en", "C.C. Wei"),
    ("Hock Tan", '"Hock Tan" Broadcom',                  "en", "Hock Tan"),
    ("Christophe Fouquet", '"Christophe Fouquet" ASML',  "en", "Christophe Fouquet"),
    ("纳德拉",   '"Satya Nadella" Microsoft',            "en", "Satya Nadella"),
    ("皮叉",     '"Sundar Pichai" Google OR Alphabet',   "en", "Sundar Pichai"),
    # P0(续)
    ("阿贝尔",   '"Greg Abel" Berkshire',                "en", "Greg Abel"),
    # P2
    ("奥特曼",   '"Sam Altman" OpenAI',                  "en", "Sam Altman"),
    ("Dario Amodei", '"Dario Amodei" Anthropic',         "en", "Dario Amodei"),
    ("哈萨比斯", '"Demis Hassabis" DeepMind',            "en", "Demis Hassabis"),
    ("但斌",     '"但斌"',                                 "zh", "Dan Bin"),   # 东方港湾董事长
]

# Google News 报错时，用 Finnhub 公司新闻作独立供应商备份。
# 同一 ticker 在单次运行内只请求一次，避免巴菲特/阿贝尔、
# 皮叉/Dario/哈萨比斯重复消耗 Finnhub 限额。
_FINNHUB_FALLBACK_TICKERS: dict[str, str] = {
    "黄仁勋": "NVDA",
    "巴菲特": "BRK.B",
    "苏妈": "AMD",
    "魏哲家": "TSM",
    "Hock Tan": "AVGO",
    "Christophe Fouquet": "ASML",
    "纳德拉": "MSFT",
    "皮叉": "GOOGL",
    "阿贝尔": "BRK.B",
    "奥特曼": "MSFT",
    "Dario Amodei": "GOOGL",
    "哈萨比斯": "GOOGL",
}

_FINNHUB_FALLBACK_ALIASES: dict[str, tuple[str, ...]] = {
    "黄仁勋": ("Jensen Huang", "Huang"),
    "巴菲特": ("Warren Buffett", "Buffett"),
    "苏妈": ("Lisa Su",),
    "魏哲家": ("C.C. Wei", "C. C. Wei"),
    "Hock Tan": ("Hock Tan",),
    "Christophe Fouquet": ("Christophe Fouquet", "Fouquet"),
    "纳德拉": ("Satya Nadella", "Nadella"),
    "皮叉": ("Sundar Pichai", "Pichai"),
    "阿贝尔": ("Greg Abel", "Abel"),
    "奥特曼": ("Sam Altman", "Altman"),
    "Dario Amodei": ("Dario Amodei", "Amodei"),
    "哈萨比斯": ("Demis Hassabis", "Hassabis"),
}

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
        pub = datetime(*pp[:6], tzinfo=UTC)
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


@retry(max_attempts=3, base_delay=1.5)
def _fetch_finnhub_company_news(
    client: finnhub.Client,
    ticker: str,
    date_from: str,
    date_to: str,
) -> list[dict]:
    return client.company_news(ticker, _from=date_from, to=date_to)


def _finnhub_mentions_for_person(
    raw: list[dict],
    *,
    person: str,
    name_en: str,
) -> list[FigureMention]:
    """Finnhub 公司新闻中只保留明确点名该人物的内容。"""
    aliases = {
        person.lower(),
        name_en.lower(),
        *(alias.lower() for alias in _FINNHUB_FALLBACK_ALIASES.get(person, ())),
    }
    items: list[FigureMention] = []
    for entry in raw or []:
        title = str(entry.get("headline") or "").strip()
        snippet = str(entry.get("summary") or "").strip()
        text = f"{title}\n{snippet}".lower()
        if not any(alias and alias in text for alias in aliases):
            continue
        try:
            timestamp = int(entry.get("datetime"))
            published_at = datetime.fromtimestamp(timestamp, tz=UTC)
        except (TypeError, ValueError, OSError, OverflowError):
            continue
        url = str(entry.get("url") or "").strip()
        if not title or not url:
            continue
        items.append(FigureMention(
            title=title,
            snippet=snippet,
            published_at=published_at,
            url=url,
            source=str(entry.get("source") or "Finnhub").strip(),
        ))
    return items


def _content_hash(person: str, item: FigureMention) -> str:
    """归一化 hash:去空白/标点/媒体后缀,让不同媒体的近似 title 共享同一 hash,
    避免"第一财经报道 X 与 搜狐转载 X" 在 7 天窗口内重复推送。"""
    title = (item.title or "").lower()
    # 去掉常见媒体后缀(如 " - MSN" / " — 新浪财经" / " - Reuters")
    title = re.sub(r"\s*[-—–]\s*[^-—–]+$", "", title).strip()
    # 去标点和空白,只保留字母数字和中日韩文字
    title = re.sub(r"[^\w一-鿿]+", "", title, flags=re.UNICODE)
    # 截前 80 字符,避免过长 title 因尾部差异错过去重
    title = title[:80]
    h = hashlib.sha1(
        f"{person}|{title}".encode(), usedforsecurity=False,
    ).hexdigest()
    return h[:16]


_HISTORICAL_YEAR_RE = re.compile(
    # 命中"历史年份关键词"——出现在标题/摘要里通常是历史发言追忆,而非当前发声
    # 仅过滤过去 7 年(2018-2024),避免误杀今年/去年(2025/2026)与不带年份的内容
    r"\b(?:2018|2019|2020|2021|2022|2023|2024)\s*年"
    r"|\b(?:2018|2019|2020|2021|2022|2023|2024)[\s\-/](?:年|股东大会|GTC|演讲|致股东信)"
    r"|当年(?:曾)?(?:说|表示|讲|认为|指出)"
    r"|年终(?:回顾|盘点|精选)"
    r"|历(?:史|年)(?:经典|名言|发言)"
)


def _passes_first_filter(item: FigureMention) -> bool:
    """第一道规则筛选:
    - 标题/摘要必须含"动词类"关键词(发言、说、表示等)
    - **不得**含历史年份/年终盘点关键词(避免推送 2019 年旧闻)
    """
    text = f"{item.title}\n{item.snippet}"
    if not _VERB_RE.search(text):
        return False
    return not _HISTORICAL_YEAR_RE.search(text)


# ---------- 状态持久化(7 天去重) ----------
def _load_pushed(state_path: Path) -> dict[str, str]:
    """{content_hash: ISO8601 推送时间}"""
    if not state_path.exists():
        return {}
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise TypeError(f"expected object, got {type(data).__name__}")
        return {str(k): str(v) for k, v in data.items() if isinstance(v, str)}
    except Exception as exc:  # noqa: BLE001
        logger.warning("pushed_figures.parse_failed exc=%s; treating as empty", exc)
        return {}


def _save_pushed(state_path: Path, data: dict[str, str]) -> None:
    """原子写入 + 失败容错:磁盘满 / 权限问题不应阻断 fetch_all 主流程。"""
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = state_path.with_suffix(state_path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        tmp.replace(state_path)
    except OSError as exc:
        logger.warning("pushed_figures.save_failed exc=%s; 7 天去重本轮失效,主流程继续", exc)


def _purge_expired(pushed: dict[str, str], now: datetime, days: int = 7) -> dict[str, str]:
    cutoff = now - timedelta(days=days)
    out = {}
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


# ---------- 入口 ----------
def fetch_all(
    state_path: Path,
    *,
    finnhub_api_key: str = "",
) -> tuple[list[FigureBundle], dict[str, str]]:
    """采集所有监控人物的过去 24h 候选发言,完成第一道筛选 + 7 天去重。

    返回 (bundles, pending_pushed):
      - bundles:供下游 figure_filter / 渲染消费
      - pending_pushed:本次"应去重"的 hash → ISO time。**fetch_all 不写盘**;
        调用方在邮件成功发送后调 commit_pushed(state_path, pending_pushed) 提交。

    为何延后写盘:figure_filter 后续 LLM 调用可能失败 → 没有 figure 渲染到邮件
    → 但若已写 pushed,这批候选未来 7 天都不会再考虑 → 用户永远看不到这批。
    延后到邮件成功才提交,失败时下次 run 还能重新评估同批候选。
    """
    start_utc, end_utc = last_24h_window()

    pushed = _purge_expired(_load_pushed(state_path), end_utc)
    new_pushed = dict(pushed)  # 含已存在 + 本次新增

    bundles: list[FigureBundle] = []
    finnhub_client = finnhub.Client(api_key=finnhub_api_key) if finnhub_api_key else None
    finnhub_cache: dict[str, list[dict]] = {}
    google_consecutive_failures = 0
    for person, query, lang, name_en in FIGURES:
        try:
            if google_consecutive_failures >= 2:
                raise RuntimeError("Google News circuit open after consecutive failures")
            raw = _fetch_google_news(query, lang=lang)
            google_consecutive_failures = 0
        except Exception as exc:  # noqa: BLE001
            google_consecutive_failures += 1
            google_error = f"{type(exc).__name__}: {redact_secrets(str(exc))[:200]}"
            ticker = _FINNHUB_FALLBACK_TICKERS.get(person)
            if finnhub_client is None or ticker is None:
                logger.error(
                    "figures.fetch_failed person=%s primary=google_news fallback=unavailable msg=%s",
                    person,
                    google_error,
                )
                bundles.append(FigureBundle(
                    person=person, query=query, person_en=name_en,
                    error=google_error,
                ))
                continue
            try:
                if ticker not in finnhub_cache:
                    finnhub_cache[ticker] = _fetch_finnhub_company_news(
                        finnhub_client,
                        ticker,
                        start_utc.strftime("%Y-%m-%d"),
                        end_utc.strftime("%Y-%m-%d"),
                    )
                raw = _finnhub_mentions_for_person(
                    finnhub_cache[ticker],
                    person=person,
                    name_en=name_en,
                )
                logger.warning(
                    "figures.fallback_used person=%s primary=google_news fallback=finnhub ticker=%s count=%d",
                    person,
                    ticker,
                    len(raw),
                )
            except Exception as fallback_exc:  # noqa: BLE001
                fallback_error = (
                    f"{type(fallback_exc).__name__}: "
                    f"{redact_secrets(str(fallback_exc))[:200]}"
                )
                logger.error(
                    "figures.fetch_failed person=%s primary_error=%s fallback_error=%s",
                    person,
                    google_error,
                    fallback_error,
                )
                bundles.append(FigureBundle(
                    person=person,
                    query=query,
                    person_en=name_en,
                    error=f"Google News {google_error}; Finnhub {fallback_error}",
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
        bundles.append(FigureBundle(person=person, query=query, person_en=name_en, items=kept))
        logger.info("figures person=%s total=%d kept=%d", person, len(raw), len(kept))

    # ── 官方源补充层:拉取 → 同管线过滤 → 合并进对应人物 bundle ──
    # 官方源候选必须和 Google News 一样通过:时间窗口 + _passes_first_filter + pushed 去重。
    # 同一事件 Google News 与官方源重复时，优先官方源作为代表来源（替换 URL/source）。
    try:
        from src.collectors import figure_official_sources  # noqa: F811 - lazy import 防循环引用
        official_mentions = figure_official_sources.fetch_all(start_utc, end_utc)
    except Exception as exc:
        logger.warning("figures.official_sources_failed exc=%s; proceeding with Google News only", exc)
        official_mentions = {}

    if official_mentions:
        for b in bundles:
            extras = official_mentions.get(b.person)
            if not extras:
                continue
            # 对官方源候选再做 _passes_first_filter + pushed 去重
            filtered: list[FigureMention] = []
            for m in extras:
                if not _passes_first_filter(m):
                    continue
                h = _content_hash(b.person, m)
                if h in pushed:
                    continue
                filtered.append(m)
                new_pushed[h] = end_utc.isoformat()

            if not filtered:
                continue

            # 合并到 bundle:优先官方源作为代表来源
            # Google News items 索引:content_hash → list index
            gn_hashes: dict[str, int] = {}
            for i, it in enumerate(b.items):
                gn_hashes[_content_hash(b.person, it)] = i

            added = 0
            replaced = 0
            for m in filtered:
                h = _content_hash(b.person, m)
                if h in gn_hashes:
                    # 同一事件，用官方源替换 Google News 条目
                    b.items[gn_hashes[h]] = m
                    replaced += 1
                else:
                    b.items.append(m)
                    added += 1

            b.items.sort(key=lambda x: x.published_at, reverse=True)
            if b.items and b.error:
                logger.warning("figures.official_recovered person=%s", b.person)
                b.error = None
            logger.info(
                "figures.official_merge person=%s added=%d replaced=%d total=%d",
                b.person, added, replaced, len(b.items),
            )

    return bundles, new_pushed


def commit_pushed(state_path: Path, pending_pushed: dict[str, str]) -> None:
    """邮件发送成功后调用 — 把 fetch_all 返回的 pending_pushed 落盘。"""
    _save_pushed(state_path, pending_pushed)


def format_published_beijing(item: FigureMention) -> str:
    return to_beijing(item.published_at).strftime("%m-%d %H:%M")
