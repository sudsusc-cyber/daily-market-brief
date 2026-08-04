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
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.collectors.stocks import StockSignal
from src.processors.html_safe import is_safe_url
from src.processors.sentiment_judge import VERDICT_THRESHOLDS
from src.renderer.text_utils import add_cjk_spacing
from src.utils.dates import to_beijing

_TEMPLATE_DIR = Path(__file__).parent / "templates"

_SENTIMENT_GAUGE_SEGMENTS = (
    # (档位, 色带颜色, 徽章/档位文字颜色)。
    ("极度恐慌", "#4F6870", "#4F6870"),
    ("偏冷", "#7C8E91", "#7C8E91"),
    ("中性", "#B8AD94", "#B8AD94"),
    ("偏热", "#A96D4F", "#A96D4F"),
    ("极度贪婪", "#7A1F2B", "#7A1F2B"),
)

_SENTIMENT_COLOR_BY_LABEL = {
    label: (bar_color, accent_color)
    for label, bar_color, accent_color in _SENTIMENT_GAUGE_SEGMENTS
}


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
    active_label = next(
        label for upper, label in VERDICT_THRESHOLDS if score < upper
    )
    # 21 个位置（每 5 分一档）兼顾精度与邮件 HTML 体积；50 分仍严格居中。
    pointer_index = min(20, max(0, round(score / 5.0)))
    pointer_percent = (pointer_index + 0.5) / 21.0 * 100.0
    bubble_region_width = 20.0
    # 气泡主体在两端吸附于色带边界，小尾巴仍留在真实 pointer_index。
    # 前/后四档覆盖 640px 桌面邮件与约 320px 移动端的气泡固有宽度。
    if pointer_index <= 3:
        bubble_left = 0.0
        bubble_align = "left"
    elif pointer_index >= 17:
        bubble_left = 100.0 - bubble_region_width
        bubble_align = "right"
    else:
        bubble_left = pointer_percent - bubble_region_width / 2.0
        bubble_align = "center"
    bubble_right = 100.0 - bubble_left - bubble_region_width
    cells = []
    for index in range(20):
        cell_score = index * 5.0
        cell_label = next(
            label for upper, label in VERDICT_THRESHOLDS if cell_score < upper
        )
        cells.append({
            "color": _SENTIMENT_COLOR_BY_LABEL[cell_label][0],
            "active": index == active_index,
        })

    score_display = f"{score:.1f}".rstrip("0").rstrip(".")
    return {
        "score": score,
        "score_display": score_display,
        # 展示文字与颜色都只信任确定性分数，避免上游 label 漂移后文字/色带矛盾。
        "label": active_label,
        "active_color": _SENTIMENT_COLOR_BY_LABEL[active_label][1],
        "coverage": verdict.get("coverage") if isinstance(verdict.get("coverage"), dict) else None,
        "pointer_index": pointer_index,
        "bubble_layout": {
            "left_width": bubble_left,
            "region_width": bubble_region_width,
            "right_width": bubble_right,
            "align": bubble_align,
        },
        "pointer_cells": [
            {"active": index == pointer_index}
            for index in range(21)
        ],
        "cells": cells,
        "segments": [
            {
                "label": segment_label,
                "color": color,
                "width": min(100.0, upper) - lower,
            }
            for (lower, (upper, segment_label)), (_, color, _accent_color) in zip(
                zip((0.0, 25.0, 40.0, 60.0, 75.0), VERDICT_THRESHOLDS, strict=True),
                _SENTIMENT_GAUGE_SEGMENTS,
                strict=True,
            )
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
    # M4 LLM 加工产物
    sentiment_verdict: dict | None = None,           # {verdict, argument}
    company_news_summary: Any | None = None,          # CompanyNewsSummary {summary_html, footnotes}
    company_news_fallback_note: str | None = None,    # 个股加工/数据源失败时的受控占位语
    figure_summaries: list[Any] | None = None,        # list[FigureSummary]
    figure_silence_note: str | None = None,           # 全员沉默时的占位语
    figure_fallback_note: str | None = None,           # 人物加工失败时的受控占位语
    figure_footnotes: list[Any] | None = None,        # list[FigureFootnote] 章节底部脚注
    macro_news_summary: Any | None = None,            # MacroNewsSummary {summary_html, footnotes}
    company_news_silence_note: str | None = None,     # 持仓无新闻时的占位语
    macro_news_silence_note: str | None = None,       # 无宏观新闻时的占位语
    macro_news_fallback_note: str | None = None,      # 宏观加工/数据源失败时的受控占位语
    frontier_labs_items: list[Any] | None = None,     # list[FrontierKeyPoint]
    frontier_labs_fallback_note: str | None = None,   # 前沿加工失败时的受控占位语
    judgment_section: dict | None = None,             # Judgment Ledger payload
) -> str:
    """
    渲染完整邮件 HTML。

    logo_cids: ticker -> CID 映射,如 {"NVDA": "logo_NVDA"}。
    个股动态与宏观视野不降级渲染原始列表，失败时使用受控占位语。
    """
    env = _build_env()
    template = env.get_template("email.html.j2")
    sentiment_gauge = _build_sentiment_gauge(sentiment_verdict)
    holdings_counts = {
        "total": len(signals),
        "us": sum(1 for signal in signals if not signal.holding.ticker.endswith(".HK")),
        "hk": sum(1 for signal in signals if signal.holding.ticker.endswith(".HK")),
    }
    html = template.render(
        signals=signals,
        generated_at=generated_at,
        logo_cids=logo_cids or {},
        header_image_url=header_image_url,
        holdings_intro=holdings_intro,
        sentiment=sentiment,
        sentiment_verdict=sentiment_verdict,
        sentiment_gauge=sentiment_gauge,
        holdings_counts=holdings_counts,
        company_news=company_news,
        company_news_summary=company_news_summary,
        company_news_fallback_note=company_news_fallback_note,
        figures=figures,
        figure_summaries=figure_summaries,
        figure_silence_note=figure_silence_note,
        figure_fallback_note=figure_fallback_note,
        figure_footnotes=figure_footnotes or [],
        macro_news=macro_news,
        macro_news_summary=macro_news_summary,
        company_news_silence_note=company_news_silence_note,
        macro_news_silence_note=macro_news_silence_note,
        macro_news_fallback_note=macro_news_fallback_note,
        buffett_13f=buffett_13f,
        frontier_labs_items=frontier_labs_items or [],
        frontier_labs_fallback_note=frontier_labs_fallback_note,
        judgment_section=judgment_section,
    )
    # 邮件客户端按解码后的 HTML 体积裁剪；只删除标签之间的排版空白，不碰正文。
    return re.sub(r"(?<=>)\s+(?=<)", "", html).strip()
