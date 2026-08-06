"""
邮件主题数据提取(Step 3)。

从 main.py 已经采集 + 加工好的数据中提取结构化摘要,作为 DeepSeek 主题生成的输入。
**不重新抓取任何数据**,纯粹复用现有变量(signals / sentiment / news 等)。

输出结构(供 prompts.py 拼装 user prompt):
{
    "solar_term": SolarTermContext,         # Step 2 节气 context
    "mood": {                               # 市场情绪
        "label": str,                       # "极度贪婪/偏热/中性/偏冷/极度恐慌"
                                            # (复用 sentiment_judge 确定性 verdict)
        "score": float,                     # 0-100 加权打分
        "cnn_fear_greed": float | None,
        "vix": float | None,
    },
    "signals": {                            # 持仓信号
        "dca_count": int,
        "lump_sum_count": int,
        "dca_tickers": list[str],
        "lump_sum_tickers": list[str],
    },
    "holdings_news_top1": str | None,       # 昨日动态首条公司新闻摘要
    "macro_news_top1": str | None,          # 宏观视野首条新闻摘要
    "email_full_text": str,                 # 邮件 HTML 转纯文本,截 2000 字
}
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from src.processors.subject.solar_terms import SolarTermContext, get_solar_term_context

logger = logging.getLogger(__name__)


# ───────────────  数据结构  ───────────────

@dataclass
class MoodInfo:
    label: str = "中性"
    score: float = 50.0
    cnn_fear_greed: float | None = None
    vix: float | None = None


@dataclass
class SignalSummary:
    dca_count: int = 0
    lump_sum_count: int = 0
    dca_tickers: list[str] = field(default_factory=list)
    lump_sum_tickers: list[str] = field(default_factory=list)


@dataclass
class SubjectData:
    solar_term: SolarTermContext
    mood: MoodInfo
    signals: SignalSummary
    holdings_news_top1: str | None
    macro_news_top1: str | None
    email_full_text: str

    def to_dict(self) -> dict[str, Any]:
        """供 prompts / 日志使用。"""
        return {
            "solar_term": {
                "current": self.solar_term.current,
                "days_into": self.solar_term.days_into,
                "days_to_next": self.solar_term.days_to_next,
                "next": self.solar_term.next,
                "phrase": self.solar_term.phrase,
            },
            "mood": {
                "label": self.mood.label,
                "score": self.mood.score,
                "cnn_fear_greed": self.mood.cnn_fear_greed,
                "vix": self.mood.vix,
            },
            "signals": {
                "dca_count": self.signals.dca_count,
                "lump_sum_count": self.signals.lump_sum_count,
                "dca_tickers": self.signals.dca_tickers,
                "lump_sum_tickers": self.signals.lump_sum_tickers,
            },
            "holdings_news_top1": self.holdings_news_top1,
            "macro_news_top1": self.macro_news_top1,
            "email_full_text_chars": len(self.email_full_text),
        }


# ───────────────  Helpers  ───────────────

def _metric_value(bundle: Any, name: str) -> float | None:
    """从 SentimentBundle 找指定指标的 current 值;不存在或失败返回 None。"""
    if not bundle or not getattr(bundle, "metrics", None):
        return None
    for m in bundle.metrics:
        if getattr(m, "name", "") == name and not getattr(m, "error", None):
            cur = getattr(m, "current", None)
            return float(cur) if cur is not None else None
    return None


def _extract_mood(sentiment_verdict: dict | None, sentiment_bundle: Any) -> MoodInfo:
    """从 sentiment_judge 输出 + 原始 bundle 提取 mood 信息。"""
    label = "中性"
    score = 50.0
    if sentiment_verdict:
        # verdict 形如 "今日情绪 · 偏热",取最后段
        raw = str(sentiment_verdict.get("verdict", "")).strip()
        # 解析 "今日情绪 · 偏热" → "偏热"
        if "·" in raw:
            label = raw.split("·")[-1].strip()
        elif raw:
            label = raw
        score = float(sentiment_verdict.get("score", 50.0))

    return MoodInfo(
        label=label,
        score=score,
        cnn_fear_greed=_metric_value(sentiment_bundle, "CNN Fear & Greed"),
        vix=_metric_value(sentiment_bundle, "VIX"),
    )


def _extract_signals(signals: list[Any]) -> SignalSummary:
    """从 list[StockSignal] 提取触发信号统计。"""
    summary = SignalSummary()
    for s in signals or []:
        sig = getattr(s, "signal", "NONE")
        ticker = getattr(getattr(s, "holding", None), "ticker", "?")
        if sig == "DCA":
            summary.dca_count += 1
            summary.dca_tickers.append(ticker)
        elif sig == "LUMP_SUM":
            summary.lump_sum_count += 1
            summary.lump_sum_tickers.append(ticker)
    return summary


# 从 summary_html 提取第一条新闻摘要(<strong>公司</strong> 摘要 [N] 形式)
_FIRST_ROW_RE = re.compile(
    r"<div[^>]*>"
    r"(?:<span[^>]*>([^<]+)</span>)?"           # 公司名 span(可选)
    r"(?:<span[^>]*>[│|]?</span>)?"             # 分隔符 span(可选)
    r"([^<]+?)"                                 # 摘要文字
    r"(?:<sup>\[\d+\]</sup>|</div>|<a)",
    re.DOTALL,
)


def _strip_tags(html: str) -> str:
    """简单去 HTML 标签 + 折叠空白。"""
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"&nbsp;|&#160;", " ", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_first_news(summary: Any) -> str | None:
    """从 CompanyNewsSummary / MacroNewsSummary 的 summary_html 提取首条新闻文字。"""
    if not summary or not getattr(summary, "summary_html", ""):
        return None
    html = summary.summary_html
    # 个股摘要使用 <div>,宏观摘要使用 <p>。两者都是受控 HTML,
    # 但标签契约不同;若只读 div,邮件主题会静默丢失宏观首条。
    m = re.search(
        r"<(?P<tag>div|p)\b[^>]*>(?P<body>.+?)</(?P=tag)\s*>",
        html,
        re.DOTALL | re.IGNORECASE,
    )
    if not m:
        return None
    text = _strip_tags(m.group("body"))
    # 摘要太长截到 80 字
    if len(text) > 80:
        text = text[:80].rstrip("。!?,;: ") + "…"
    return text or None


# ───────────────  主入口  ───────────────

def extract_subject_data(
    *,
    today_bj: date,
    signals: list[Any] | None,
    sentiment_verdict: dict | None,
    sentiment_bundle: Any,
    company_news_summary: Any,
    macro_news_summary: Any,
    email_html: str,
    full_text_max_chars: int = 2000,
) -> SubjectData:
    """主入口:整合 main.py 已有数据 → 主题生成需要的结构化摘要。"""
    solar_term = get_solar_term_context(today_bj)
    mood = _extract_mood(sentiment_verdict, sentiment_bundle)
    signals_summary = _extract_signals(signals or [])

    holdings_news = _extract_first_news(company_news_summary)
    macro_news = _extract_first_news(macro_news_summary)

    full_text = _strip_tags(email_html)
    if len(full_text) > full_text_max_chars:
        full_text = full_text[:full_text_max_chars] + "…"

    data = SubjectData(
        solar_term=solar_term,
        mood=mood,
        signals=signals_summary,
        holdings_news_top1=holdings_news,
        macro_news_top1=macro_news,
        email_full_text=full_text,
    )
    logger.info(
        "subject.extract solar_term=%s mood=%s/%.1f dca=%d lump=%d "
        "holdings_news=%s macro_news=%s text_chars=%d",
        solar_term.current, mood.label, mood.score,
        signals_summary.dca_count, signals_summary.lump_sum_count,
        bool(holdings_news), bool(macro_news), len(full_text),
    )
    return data
