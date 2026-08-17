"""
持仓公司昨日新闻采集(模块 1)。

数据源策略(PLAN 第 4 节模块 1):
  - 美股 + ADR(MSFT/COST/AAPL/NVDA/TSM/MCO/GOOG/BRK.B/KO/AXP/MA/LIN):Finnhub /company-news
  - 港股(0700.HK 腾讯 / 9992.HK 泡泡玛特):Finnhub 港股覆盖弱,降级到 Google News 中文搜索

时间窗口:发送时点向前滚动 24 小时，覆盖目标美股交易日全天与盘后公告。

两层去重:
  1. 跨天 7 天 hash 去重:已推送过的 (ticker, 归一化标题 hash) 不再出现,
     状态持久化到 state/pushed_company_news.json。
  2. 同日 SequenceMatcher 模糊去重:同一 ticker 同一天内相似标题(≥0.72)
     视为同一事件,仅保留最新一条。

延后写盘——邮件发送成功后才 commit 跨天去重,失败时下次 run 仍能重新评估。

输出:每只股票一个 CompanyNewsBundle,含若干 NewsItem(标题/发布时间/链接/来源)。
M3 阶段不做 LLM 摘要,直接把列表传给模板渲染。
M4 起 processors/news_summarizer.py 会读这些 bundle 输出段落叙述。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import urllib.parse
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path

import finnhub  # type: ignore[import-untyped]

from src.config import Holding
from src.utils.dates import last_24h_window, to_beijing
from src.utils.fetch_rss import fetch_rss
from src.utils.retry import retry
from src.utils.secrets import redact_secrets

logger = logging.getLogger(__name__)


@dataclass
class NewsItem:
    """单条新闻"""
    title: str
    published_at: datetime  # aware,UTC
    url: str
    source: str  # 媒体名
    summary: str = ""  # 摘要 / 描述,Finnhub 提供;用于相关性过滤


@dataclass
class CompanyNewsBundle:
    """单只持仓的新闻包(可能为空)"""
    holding: Holding
    items: list[NewsItem] = field(default_factory=list)
    error: str | None = None
    data_source: str = ""  # "finnhub" / "google_news_cn" / "google_news_en"


# 相关性过滤:Finnhub 的 /company-news 把"同板块 / 竞品 / 大盘"都贴上 ticker 标签
# (NVDA 24h 249 条里只有 1 条真正是 NVDA 主题)。这里要求标题或摘要至少含其中之一关键词。
# 港股走 Google News 中文搜索,query 已是公司名,相关性较高,不做该过滤。
_RELEVANCE_KEYWORDS: dict[str, list[str]] = {
    "MSFT": ["MSFT", "Microsoft", "Satya Nadella", "Azure", "Copilot", "Windows", "Xbox"],
    "COST": ["COST", "Costco", "Kirkland"],
    "AAPL": ["AAPL", "Apple", "iPhone", "iPad", "Mac ", "MacBook", "Tim Cook", "Vision Pro", "Apple "],
    "NVDA": ["NVDA", "NVIDIA", "Nvidia", "Jensen Huang", "GeForce", "CUDA", "RTX", "Blackwell"],
    "TSM": ["TSM", "TSMC", "Taiwan Semiconductor", "台积电"],
    "MCO": ["MCO", "Moody", "Moody's"],
    "GOOG": ["GOOG", "GOOGL", "Google", "Alphabet", "YouTube", "Sundar Pichai", "Pixel ", "Gemini"],
    "BRK.B": ["BRK", "Berkshire", "Buffett", "GEICO", "BNSF"],
    "KO": ["Coca-Cola", "Coca Cola", "Coke", " KO "],  # KO 单字母太宽,只在带空格时匹配
    "AXP": ["AXP", "American Express", "Amex"],
    # MA / LIN 同 KO:2 字母 ticker 太宽(MA 会命中 magazine 等,LIN 命中 link 等),
    # 不放裸 ticker,只用公司名匹配。
    "MA": ["Mastercard", "Master Card", "MasterCard"],
    "LIN": ["Linde", "Linde plc", "林德"],
}


def _is_relevant(item: NewsItem, ticker: str) -> bool:
    """标题或 summary 至少包含一个关键词(大小写不敏感)才视为相关"""
    keywords = _RELEVANCE_KEYWORDS.get(ticker)
    if not keywords:
        return True  # 没列入字典(不应发生)则降级为不过滤
    haystack = f" {item.title}\n{item.summary} "  # 两端 space 让 " KO " 类规则可命中
    haystack_lower = haystack.lower()
    return any(kw.lower() in haystack_lower for kw in keywords)


# ---------- Finnhub(美股 / ADR) ----------
@retry(max_attempts=3, base_delay=1.5)
def _fetch_finnhub(client: finnhub.Client, ticker: str, date_from: str, date_to: str) -> list[dict]:
    return client.company_news(ticker, _from=date_from, to=date_to)


def _collect_via_finnhub(client: finnhub.Client, holding: Holding) -> CompanyNewsBundle:
    # 发送发生在美股收盘后；滚动 24h 才能覆盖完整交易时段和盘后公告。
    start_utc, end_utc = last_24h_window()
    date_from = start_utc.strftime("%Y-%m-%d")
    date_to = end_utc.strftime("%Y-%m-%d")
    try:
        raw = _fetch_finnhub(client, holding.ticker, date_from, date_to)
    except Exception as exc:  # noqa: BLE001 — 故障降级,不向上抛
        safe_msg = redact_secrets(str(exc))[:200]
        logger.error(
            "finnhub.fetch_failed ticker=%s exc_type=%s msg=%s",
            holding.ticker, type(exc).__name__, safe_msg,
        )
        return CompanyNewsBundle(
            holding=holding, error=f"Finnhub 异常: {type(exc).__name__}: {safe_msg}",
            data_source="finnhub",
        )

    items: list[NewsItem] = []
    for n in raw or []:
        ts = n.get("datetime")
        if ts is None:
            continue
        # Finnhub 偶发返回脏数据(0 / 负值 / 毫秒级时间戳 ts*1000),
        # int(ts) 不抛但产出 1970 年或 5000 年的 datetime,后续窗口过滤会全部 drop
        # 但日志看不出原因。先把范围外的丢弃并跳过,避免 fromtimestamp 上限抛 OverflowError。
        try:
            ts_int = int(ts)
        except (TypeError, ValueError):
            continue
        # 合理 unix 秒区间:2001-01-01 ~ 2100-01-01
        if not (978307200 <= ts_int <= 4102444800):
            continue
        pub = datetime.fromtimestamp(ts_int, tz=UTC)
        # Finnhub date filter 是 UTC,精确到日;我们再过滤一遍北京日窗口
        if not (start_utc <= pub < end_utc):
            continue
        items.append(NewsItem(
            title=str(n.get("headline") or "").strip(),
            published_at=pub,
            url=str(n.get("url") or ""),
            source=str(n.get("source") or "Finnhub"),
            summary=str(n.get("summary") or "").strip(),
        ))
    # 相关性过滤(Finnhub 同板块标签噪音很重)
    raw_count = len(items)
    items = [it for it in items if _is_relevant(it, holding.ticker)]
    logger.info("company_news.relevance ticker=%s raw=%d kept=%d",
                holding.ticker, raw_count, len(items))
    items.sort(key=lambda x: x.published_at, reverse=True)
    before_fuzzy = len(items)
    items = _dedupe_fuzzy(items)
    if len(items) < before_fuzzy:
        logger.info(
            "company_news.fuzzy_dedup ticker=%s before=%d after=%d (同日模糊去重 threshold=0.72)",
            holding.ticker, before_fuzzy, len(items),
        )
    return CompanyNewsBundle(holding=holding, items=items, data_source="finnhub")


# ---------- Google News(港股主路径 / Finnhub 故障时的美股备路径) ----------
@retry(max_attempts=3, base_delay=1.5)
def _fetch_google_news(query: str, *, lang: str) -> list[NewsItem]:
    """Google News RSS,过去 24 小时。"""
    q = urllib.parse.quote(query)
    if lang == "zh":
        url = f"https://news.google.com/rss/search?q={q}+when:1d&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
    else:
        url = f"https://news.google.com/rss/search?q={q}+when:1d&hl=en-US&gl=US&ceid=US:en"
    feed = fetch_rss(url)
    items: list[NewsItem] = []
    for e in feed.entries or []:
        pp = getattr(e, "published_parsed", None) or getattr(e, "updated_parsed", None)
        if not pp:
            continue
        pub = datetime(*pp[:6], tzinfo=UTC)
        items.append(NewsItem(
            title=str(getattr(e, "title", "") or "").strip(),
            published_at=pub,
            url=str(getattr(e, "link", "") or ""),
            source=str(getattr(getattr(e, "source", None), "title", "") or "Google News"),
        ))
    return items


def _fetch_google_news_zh(query: str) -> list[NewsItem]:
    """保留原有入口，便于旧调用方与测试继续 monkeypatch。"""
    return _fetch_google_news(query, lang="zh")


_HK_QUERY_FALLBACK = {
    "0700.HK": "腾讯控股",
    "9992.HK": "泡泡玛特",
}


# ---------- 同日 SequenceMatcher 模糊去重(Phase 2) ----------
def _normalize_for_similarity(text: str) -> str:
    """归一化:去空白 + 去标点 + 小写。"""
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"[,。!?:;、—\-()()【】《》\"\"'']", "", text)
    return text.lower()


def _similar(a: str, b: str, threshold: float = 0.72) -> bool:
    """SequenceMatcher 相似度 ≥ threshold 视为同一事件。

    阈值 0.72 比 figure_filter 的 0.6 更严:
    - figure_filter 比的是 LLM 已提炼的中文观点(短而密),0.6 合理
    - 新闻标题更长、含模板化前缀,0.6 有误合并风险
    """
    return SequenceMatcher(
        None, _normalize_for_similarity(a), _normalize_for_similarity(b),
    ).ratio() >= threshold


def _dedupe_fuzzy(items: list[NewsItem]) -> list[NewsItem]:
    """对一只 ticker 的同日候选做模糊去重,保留发布时间最新的一条。

    输入假设已按 published_at 降序排序。
    """
    kept: list[NewsItem] = []
    for it in items:
        if any(_similar(it.title, k.title) for k in kept):
            continue
        kept.append(it)
    return kept


def _collect_via_google_news(
    holding: Holding,
    *,
    lang: str | None = None,
) -> CompanyNewsBundle:
    is_hk = holding.ticker.endswith(".HK")
    if lang is None:
        lang = "zh" if is_hk else "en"
    query = _HK_QUERY_FALLBACK.get(
        holding.ticker,
        f'"{holding.name}" OR "{holding.ticker}"',
    )
    start_utc, end_utc = last_24h_window()
    try:
        if lang == "zh":
            all_items = _fetch_google_news_zh(query)
        else:
            all_items = _fetch_google_news(query, lang=lang)
    except Exception as exc:  # noqa: BLE001
        safe_msg = redact_secrets(str(exc))[:200]
        logger.error(
            "google_news.fetch_failed ticker=%s query=%s exc_type=%s msg=%s",
            holding.ticker, query, type(exc).__name__, safe_msg,
        )
        return CompanyNewsBundle(
            holding=holding, error=f"Google News 异常: {type(exc).__name__}: {safe_msg}",
            data_source=f"google_news_{lang}",
        )
    items = [n for n in all_items if start_utc <= n.published_at < end_utc]
    if not is_hk:
        # 美股备路径仍执行与 Finnhub 一致的公司相关性门。
        items = [item for item in items if _is_relevant(item, holding.ticker)]
    items.sort(key=lambda x: x.published_at, reverse=True)
    items = _dedupe_fuzzy(items)
    return CompanyNewsBundle(holding=holding, items=items, data_source=f"google_news_{lang}")


# ---------- 状态持久化(7 天去重) ----------
def _content_hash(ticker: str, item: NewsItem) -> str:
    """归一化 hash:去媒体后缀 / 去标点空白 / 截 80 字 / 加 ticker 前缀。

    与 figures._content_hash 同形,仅 person → ticker。
    不包含 URL —— 通讯社 syndication 的 URL 各家不同,但内容是同一条。
    包含 ticker 前缀 —— 同一标题打不同股票算两条 hash,避免误合并。
    """
    title = (item.title or "").lower()
    title = re.sub(r"\s*[-—–]\s*[^-—–]+$", "", title).strip()
    title = re.sub(r"[^\w一-鿿]+", "", title, flags=re.UNICODE)
    title = title[:80]
    h = hashlib.sha1(
        f"{ticker}|{title}".encode(), usedforsecurity=False,
    ).hexdigest()
    return h[:16]


def _load_pushed_news(state_path: Path) -> dict[str, str]:
    """{content_hash: ISO8601 推送时间}"""
    if not state_path.exists():
        return {}
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise TypeError(f"expected object, got {type(data).__name__}")
        return {str(k): str(v) for k, v in data.items() if isinstance(v, str)}
    except Exception as exc:  # noqa: BLE001
        logger.warning("pushed_company_news.parse_failed exc=%s; treating as empty", exc)
        return {}


def _save_pushed_news(state_path: Path, data: dict[str, str]) -> None:
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
        logger.warning("pushed_company_news.save_failed exc=%s; 7 天去重本轮失效,主流程继续", exc)


def _purge_expired_news(pushed: dict[str, str], now: datetime, days: int = 7) -> dict[str, str]:
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


# ---------- 入口 ----------
def fetch_one(holding: Holding, finnhub_client: finnhub.Client) -> CompanyNewsBundle:
    """根据 ticker 采集单只持仓的昨日新闻。

    两个提供商交叉降级：
    - 美股 / ADR: Finnhub 主 → Google News 英文备
    - 港股: Google News 中文主 → Finnhub 备

    只在主源明确报错时切换；“成功但无新闻”仍是合法结果，
    不用备源噪声填充真实的安静日。
    """
    if holding.ticker.endswith(".HK"):
        bundle = _collect_via_google_news(holding)
        if bundle.error:
            primary_error = bundle.error
            fallback = _collect_via_finnhub(finnhub_client, holding)
            if not fallback.error:
                logger.warning(
                    "company_news.fallback_used ticker=%s primary=google_news_cn fallback=finnhub",
                    holding.ticker,
                )
                bundle = fallback
            else:
                bundle.error = f"{primary_error}; 备用 {fallback.error}"
                bundle.data_source = "google_news_cn+finnhub"
    else:
        bundle = _collect_via_finnhub(finnhub_client, holding)
        if bundle.error:
            primary_error = bundle.error
            fallback = _collect_via_google_news(holding, lang="en")
            if not fallback.error:
                logger.warning(
                    "company_news.fallback_used ticker=%s primary=finnhub fallback=google_news_en",
                    holding.ticker,
                )
                bundle = fallback
            else:
                bundle.error = f"{primary_error}; 备用 {fallback.error}"
                bundle.data_source = "finnhub+google_news_en"
    logger.info(
        "company_news ticker=%s source=%s count=%d error=%s",
        holding.ticker, bundle.data_source, len(bundle.items), bundle.error,
    )
    return bundle


def fetch_all(
    holdings: list[Holding],
    finnhub_api_key: str,
    *,
    state_path: Path,
) -> tuple[list[CompanyNewsBundle], dict[str, str]]:
    """串行采集所有持仓昨日新闻 + 7 天 hash 去重。

    返回 (bundles, pending_pushed):
      - bundles:供下游 news_summarizer / 渲染消费
      - pending_pushed:本次"应去重"的 hash → ISO time。**fetch_all 不写盘**;
        调用方在邮件成功发送后调 commit_pushed 提交。

    为何延后写盘:news_summarizer / 渲染 / SMTP 任一失败 → 没有真发到用户
      → 已 push 的 hash 未来 7 天都不会再考虑 → 用户永远看不到这批。
      延后到邮件成功才提交,失败时下次 run 还能重新评估同批候选。
      与 figures.fetch_all 一致。
    """
    _, end_utc = last_24h_window()
    pushed = _purge_expired_news(_load_pushed_news(state_path), end_utc)
    new_pushed = dict(pushed)

    client = finnhub.Client(api_key=finnhub_api_key)
    bundles: list[CompanyNewsBundle] = []
    for h in holdings:
        bundle = fetch_one(h, client)
        if bundle.error or not bundle.items:
            bundles.append(bundle)
            continue
        kept: list[NewsItem] = []
        for it in bundle.items:
            hh = _content_hash(h.ticker, it)
            if hh in pushed:
                continue
            kept.append(it)
            new_pushed[hh] = end_utc.isoformat()
        if len(kept) < len(bundle.items):
            logger.info(
                "company_news.dedup ticker=%s before=%d after=%d (跨 7 天去重)",
                h.ticker, len(bundle.items), len(kept),
            )
        bundle.items = kept
        bundles.append(bundle)
    return bundles, new_pushed


def commit_pushed(state_path: Path, pending_pushed: dict[str, str]) -> None:
    """邮件发送成功后调用 — 把 fetch_all 返回的 pending_pushed 落盘。"""
    _save_pushed_news(state_path, pending_pushed)


def format_published_beijing(item: NewsItem) -> str:
    """模板用的发布时间格式,北京时间 'MM-DD HH:MM'"""
    return to_beijing(item.published_at).strftime("%m-%d %H:%M")
