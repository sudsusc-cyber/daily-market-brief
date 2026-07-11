"""
邮件 HTML 渲染入口。

输入:
  - signals: list[StockSignal]
  - generated_at: datetime(应该是 Asia/Shanghai)
  - sentiment / company_news / figures / macro_news / buffett_13f(M3 起)

输出:
  - HTML 字符串(完整的 <!doctype html>...</html>)

模板位置:src/renderer/templates/email.html.j2
"""

from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.collectors.stocks import StockSignal
from src.processors.html_safe import is_safe_url
from src.renderer.text_utils import add_cjk_spacing
from src.utils.dates import to_beijing

_TEMPLATE_DIR = Path(__file__).parent / "templates"

_SENTIMENT_GAUGE_SEGMENTS = (
    ("极度恐慌", "#4F6870"),
    ("偏冷", "#7C8E91"),
    ("中性", "#B8AD94"),
    ("偏热", "#A96D4F"),
    ("极度贪婪", "#7A1F2B"),
)


def _finite_number(value: float | None) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _build_sentiment_gauge(verdict: dict | None) -> dict | None:
    """把 0-100 情绪分数转换成邮件兼容的 20 格静态仪表。

    不使用 CSS 定位或远程图片：表格单元格在 QQ、Outlook 和移动端邮件
    客户端中的表现更稳定。20 格对应每格 5 分，正文仍显示精确分数。
    """
    if not isinstance(verdict, dict):
        return None
    score = _finite_number(verdict.get("score"))
    if score is None:
        return None

    score = max(0.0, min(100.0, score))
    active_index = min(19, int(score / 5.0))
    active_segment_index = min(4, active_index // 4)
    raw_label = str(verdict.get("verdict") or "").strip()
    label = raw_label.rsplit("·", 1)[-1].strip() if raw_label else ""

    cells = []
    for index in range(20):
        segment_index = min(4, index // 4)
        cells.append({
            "color": _SENTIMENT_GAUGE_SEGMENTS[segment_index][1],
            "active": index == active_index,
        })

    score_display = f"{score:.1f}".rstrip("0").rstrip(".")
    return {
        "score": score,
        "score_display": score_display,
        "label": label,
        "active_color": _SENTIMENT_GAUGE_SEGMENTS[active_segment_index][1],
        "cells": cells,
        "segments": [
            {"label": segment_label, "color": color}
            for segment_label, color in _SENTIMENT_GAUGE_SEGMENTS
        ],
    }


def _filter_price(value: float | None) -> str:
    """浮点 → 千分位 + 2 位小数;None 或 NaN 返回长破折号"""
    value = _finite_number(value)
    if value is None:
        return "—"
    try:
        return f"{value:,.2f}"
    except (TypeError, ValueError):
        return "—"


def _filter_pct(value: float | None) -> str:
    """浮点(0.092 形式)→ '+9.2%'/'-9.2%';None 返回长破折号"""
    value = _finite_number(value)
    if value is None:
        return "—"
    try:
        return f"{value * 100:+.1f}%"
    except (TypeError, ValueError):
        return "—"


def _filter_metric_num(value: float | None, unit: str = "") -> str:
    """情绪指标当前值/前一日值。**只输出数字**(单位由模板在指标名旁单独标注),
    使 right-align 列严格小数点对齐。unit 参数保留为兼容,内部忽略。"""
    _ = unit  # noqa: F841
    value = _finite_number(value)
    if value is None:
        return "—"
    try:
        absv = abs(value)
        if absv >= 100:
            return f"{value:,.1f}"
        return f"{value:.2f}"
    except (TypeError, ValueError):
        return "—"


def _filter_metric_delta(delta: float | None, unit: str = "") -> str:
    """情绪指标变化值,带正负号。None / 接近零 → '—',避免 '+0.00' 噪音。
    unit 参数为兼容保留,不再附在数字尾(单位由模板侧标注)。"""
    _ = unit  # noqa: F841
    delta = _finite_number(delta)
    if delta is None:
        return "—"
    try:
        absv = abs(delta)
        # 浮点容差:绝对值 < 0.005(2 位小数四舍五入会显示 0.00)视为无变化
        if absv < 0.005:
            return "—"
        if absv >= 100:
            return f"{delta:+,.1f}"
        return f"{delta:+.2f}"
    except (TypeError, ValueError):
        return "—"


def _filter_bj_time(dt: datetime | None) -> str:
    """datetime → 北京时间 'MM-DD HH:MM' 字符串"""
    if dt is None:
        return ""
    try:
        return to_beijing(dt).strftime("%m-%d %H:%M")
    except Exception:  # noqa: BLE001
        return ""


def _filter_bj_date_cn(dt: datetime | None) -> str:
    """datetime → 北京日期 'M 月 D 日'(无时分,无前导零)。"""
    if dt is None:
        return ""
    try:
        bj = to_beijing(dt)
        return f"{bj.month} 月 {bj.day} 日"
    except Exception:  # noqa: BLE001
        return ""


def _filter_iso_date_md(value: str | None) -> str:
    """'YYYY-MM-DD' → 'M/D'(无前导零)。损坏时返回空串。

    用于 sentiment.stale_from 等紧凑日期标注。
    """
    if not value or not isinstance(value, str):
        return ""
    parts = value.split("-")
    if len(parts) != 3:
        return ""
    try:
        return f"{int(parts[1])}/{int(parts[2])}"
    except (ValueError, TypeError):
        return ""


def _filter_safe_url(url: str | None) -> str:
    """URL 白名单过滤 — 给模板里所有 <a href="{{ x | safe_url }}"> 用。

    不安全 / 空 → 返回 "":浏览器视为空 href(指向当前页),绝不会执行
    javascript:/data:/vbscript: 协议。是模板侧的 defense in depth — 即使
    上游 collector / processor 有遗漏,模板也不会成为 XSS 出口。
    """
    return url if is_safe_url(url) else ""


def _build_env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(_TEMPLATE_DIR),
        autoescape=select_autoescape(["html"]),
        trim_blocks=False,
        lstrip_blocks=False,
    )
    env.filters["price"] = _filter_price
    env.filters["pct"] = _filter_pct
    env.filters["metric_num"] = _filter_metric_num
    env.filters["metric_delta"] = _filter_metric_delta
    env.filters["bj_time"] = _filter_bj_time
    env.filters["bj_date_cn"] = _filter_bj_date_cn
    env.filters["iso_date_md"] = _filter_iso_date_md
    env.filters["safe_url"] = _filter_safe_url
    env.filters["cjk_spaced"] = add_cjk_spacing
    return env


def render_email(
    *,
    signals: list[StockSignal],
    generated_at: datetime,
    logo_cids: dict[str, str] | None = None,
    # M5 刊头图
    header_image_url: str | None = None,
    # M5 LLM 改写的持仓引言(无值时模板退回原 M2 文案)
    holdings_intro: str | None = None,
    # M3 原始数据(始终渲染指标小表 / 错误兜底)
    sentiment: Any | None = None,            # SentimentBundle
    company_news: list[Any] | None = None,    # list[CompanyNewsBundle]
    figures: list[Any] | None = None,         # list[FigureBundle]
    macro_news: list[Any] | None = None,      # list[MacroFeedBundle]
    buffett_13f: Any | None = None,           # BuffettBundle
    # M4 LLM 加工产物(若为 None,模板自动降级到 M3 原始数据)
    sentiment_verdict: dict | None = None,           # {verdict, argument}
    company_news_summary: Any | None = None,          # CompanyNewsSummary {summary_html, footnotes}
    figure_summaries: list[Any] | None = None,        # list[FigureSummary]
    figure_silence_note: str | None = None,           # 全员沉默时的占位语
    figure_footnotes: list[Any] | None = None,        # list[FigureFootnote] 章节底部脚注
    macro_news_summary: Any | None = None,            # MacroNewsSummary {summary_html, footnotes}
    company_news_silence_note: str | None = None,     # 持仓无新闻时的占位语
    macro_news_silence_note: str | None = None,       # 无宏观新闻时的占位语
    frontier_labs_items: list[Any] | None = None,     # list[FrontierKeyPoint]
    judgment_section: dict | None = None,             # Judgment Ledger payload
) -> str:
    """
    渲染完整邮件 HTML。

    logo_cids: ticker -> CID 映射,如 {"NVDA": "logo_NVDA"}。
    M4 加工产物若为 None,模板降级渲染 M3 原始数据列表。
    """
    env = _build_env()
    template = env.get_template("email.html.j2")
    sentiment_gauge = _build_sentiment_gauge(sentiment_verdict)
    return template.render(
        signals=signals,
        generated_at=generated_at,
        logo_cids=logo_cids or {},
        header_image_url=header_image_url,
        holdings_intro=holdings_intro,
        sentiment=sentiment,
        sentiment_verdict=sentiment_verdict,
        sentiment_gauge=sentiment_gauge,
        company_news=company_news,
        company_news_summary=company_news_summary,
        figures=figures,
        figure_summaries=figure_summaries,
        figure_silence_note=figure_silence_note,
        figure_footnotes=figure_footnotes or [],
        macro_news=macro_news,
        macro_news_summary=macro_news_summary,
        company_news_silence_note=company_news_silence_note,
        macro_news_silence_note=macro_news_silence_note,
        buffett_13f=buffett_13f,
        frontier_labs_items=frontier_labs_items or [],
        judgment_section=judgment_section,
    )
