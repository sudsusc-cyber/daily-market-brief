"""
持仓公司昨日新闻采集(模块 1)。

数据源策略(PLAN 第 4 节模块 1):
  - 美股 + ADR(MSFT/COST/AAPL/NVDA/TSM/MCO/GOOG/BRK.B/KO/AXP):Finnhub /company-news
  - 港股(0700.HK 腾讯 / 9992.HK 泡泡玛特):Finnhub 港股覆盖弱,降级到 Google News 中文搜索

时间窗口:北京时间昨日 00:00 ~ 今日 00:00(用户视角的"昨日")。

输出:每只股票一个 CompanyNewsBundle,含若干 NewsItem(标题/发布时间/链接/来源)。
M3 阶段不做 LLM 摘要,直接把列表传给模板渲染。
M4 起 processors/news_summarizer.py 会读这些 bundle 输出段落叙述。
"""

from __future__ import annotations

import logging
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone

import finnhub  # type: ignore[import-untyped]

from src.config import Holding
from src.utils.dates import to_beijing, yesterday_beijing_window
from src.utils.fetch_rss import fetch_rss
from src.utils.retry import retry

logger = logging.getLogger(__name__)


@dataclass
class NewsItem:
    """单条新闻"""
    title: str
    published_at: datetime  # aware,UTC
    url: str
    source: str  # 媒体名


@dataclass
class CompanyNewsBundle:
    """单只持仓的新闻包(可能为空)"""
    holding: Holding
    items: list[NewsItem] = field(default_factory=list)
    error: str | None = None
    data_source: str = ""  # "finnhub" / "google_news_cn"


# ---------- Finnhub(美股 / ADR) ----------
@retry(max_attempts=3, base_delay=1.5)
def _fetch_finnhub(client: finnhub.Client, ticker: str, date_from: str, date_to: str) -> list[dict]:
    return client.company_news(ticker, _from=date_from, to=date_to)


def _collect_via_finnhub(client: finnhub.Client, holding: Holding) -> CompanyNewsBundle:
    start_utc, end_utc = yesterday_beijing_window()
    date_from = start_utc.strftime("%Y-%m-%d")
    date_to = end_utc.strftime("%Y-%m-%d")
    try:
        raw = _fetch_finnhub(client, holding.ticker, date_from, date_to)
    except Exception as exc:  # noqa: BLE001 — 故障降级,不向上抛
        logger.exception("finnhub.fetch_failed ticker=%s", holding.ticker)
        return CompanyNewsBundle(
            holding=holding, error=f"Finnhub 异常: {type(exc).__name__}: {exc}",
            data_source="finnhub",
        )

    items: list[NewsItem] = []
    for n in raw or []:
        ts = n.get("datetime")
        if ts is None:
            continue
        pub = datetime.fromtimestamp(int(ts), tz=timezone.utc)
        # Finnhub date filter 是 UTC,精确到日;我们再过滤一遍北京日窗口
        if not (start_utc <= pub < end_utc):
            continue
        items.append(NewsItem(
            title=str(n.get("headline") or "").strip(),
            published_at=pub,
            url=str(n.get("url") or ""),
            source=str(n.get("source") or "Finnhub"),
        ))
    items.sort(key=lambda x: x.published_at, reverse=True)
    return CompanyNewsBundle(holding=holding, items=items, data_source="finnhub")


# ---------- Google News 中文(港股降级) ----------
@retry(max_attempts=3, base_delay=1.5)
def _fetch_google_news_zh(query: str) -> list[NewsItem]:
    """中文 Google News RSS,过去 24 小时"""
    q = urllib.parse.quote(query)
    url = f"https://news.google.com/rss/search?q={q}+when:1d&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
    feed = fetch_rss(url)
    items: list[NewsItem] = []
    for e in feed.entries or []:
        pp = getattr(e, "published_parsed", None) or getattr(e, "updated_parsed", None)
        if not pp:
            continue
        pub = datetime(*pp[:6], tzinfo=timezone.utc)
        items.append(NewsItem(
            title=str(getattr(e, "title", "") or "").strip(),
            published_at=pub,
            url=str(getattr(e, "link", "") or ""),
            source=str(getattr(getattr(e, "source", None), "title", "") or "Google News"),
        ))
    return items


_HK_QUERY_FALLBACK = {
    "0700.HK": "腾讯控股",
    "9992.HK": "泡泡玛特",
}


def _collect_via_google_news(holding: Holding) -> CompanyNewsBundle:
    query = _HK_QUERY_FALLBACK.get(holding.ticker, holding.name)
    start_utc, end_utc = yesterday_beijing_window()
    try:
        all_items = _fetch_google_news_zh(query)
    except Exception as exc:  # noqa: BLE001
        logger.exception("google_news.fetch_failed ticker=%s query=%s", holding.ticker, query)
        return CompanyNewsBundle(
            holding=holding, error=f"Google News 异常: {type(exc).__name__}: {exc}",
            data_source="google_news_cn",
        )
    items = [n for n in all_items if start_utc <= n.published_at < end_utc]
    items.sort(key=lambda x: x.published_at, reverse=True)
    return CompanyNewsBundle(holding=holding, items=items, data_source="google_news_cn")


# ---------- 入口 ----------
def fetch_one(holding: Holding, finnhub_client: finnhub.Client) -> CompanyNewsBundle:
    """根据 ticker 选择对应数据源采集单只持仓的昨日新闻"""
    if holding.ticker.endswith(".HK"):
        bundle = _collect_via_google_news(holding)
    else:
        bundle = _collect_via_finnhub(finnhub_client, holding)
    logger.info(
        "company_news ticker=%s source=%s count=%d error=%s",
        holding.ticker, bundle.data_source, len(bundle.items), bundle.error,
    )
    return bundle


def fetch_all(holdings: list[Holding], finnhub_api_key: str) -> list[CompanyNewsBundle]:
    """串行采集所有持仓昨日新闻"""
    client = finnhub.Client(api_key=finnhub_api_key)
    return [fetch_one(h, client) for h in holdings]


def format_published_beijing(item: NewsItem) -> str:
    """模板用的发布时间格式,北京时间 'MM-DD HH:MM'"""
    return to_beijing(item.published_at).strftime("%m-%d %H:%M")
