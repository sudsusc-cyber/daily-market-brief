"""
邮件 HTML 渲染入口。

输入:
  - signals: list[StockSignal]
  - generated_at: datetime(应该是 Asia/Shanghai)
  - sentiment / company_news / figures / macro_news / buffett_13f / jiangsu_fuel_alert

输出:
  - HTML 字符串(完整的 <!doctype html>...</html>)

模板位置:src/renderer/templates/email.html.j2
"""

from __future__ import annotations

import logging
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.collectors.stocks import StockSignal
from src.processors.html_safe import is_safe_url
from src.processors.sentiment_judge import VERDICT_THRESHOLDS, one_sentence_summary
from src.renderer.text_utils import add_cjk_spacing
from src.utils.dates import to_beijing
from src.utils.email_typography import EMAIL_EDITORIAL_SERIF, EMAIL_NUMERIC_FEATURES
from src.valuation.models import ValuationDisplay

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_EMAIL_HTML_BUDGET_BYTES = 98_304  # 96 KiB, 给客户端 100 KiB 裁剪线留余量
_DUPLICATE_SOURCE_LIST_RE = re.compile(
    r'<tr\s+data-source-list="true">.*?</tr>',
    re.IGNORECASE | re.DOTALL,
)

logger = logging.getLogger(__name__)

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
    """把 0-100 情绪分数转换成邮件兼容的静态仪表。

    不使用 CSS 定位或远程图片：数字气泡、尾巴、黑色箭头与色带共用
    同一个 0-100 百分比坐标。表格单元格在 QQ、Outlook 和移动端邮件中
    的表现比 absolute positioning 更稳定。
    """
    if not isinstance(verdict, dict):
        return None
    score = _finite_number(verdict.get("score"))
    if score is None:
        return None

    score = max(0.0, min(100.0, score))
    active_label = next(
        label for upper, label in VERDICT_THRESHOLDS if score < upper
    )

    # 气泡的 20% 容器以真实分数为中心;只在两端吸附边界防止溢出。
    bubble_region_width = 20.0
    if score <= bubble_region_width / 2.0:
        bubble_left = 0.0
        bubble_align = "left"
    elif score >= 100.0 - bubble_region_width / 2.0:
        bubble_left = 100.0 - bubble_region_width
        bubble_align = "right"
    else:
        bubble_left = score - bubble_region_width / 2.0
        bubble_align = "center"
    bubble_right = 100.0 - bubble_left - bubble_region_width

    # 两个箭头使用同一个 5% 宽的标记格。5% 在窄屏邮件中也足够容纳
    # 14px 的彩色尾巴，避免字形溢出后由客户端产生不对称的视觉偏移。
    # 中段标记格中心严格等于 score%;0/100 分时吸附边界。
    pointer_region_width = 5.0
    if score <= pointer_region_width / 2.0:
        pointer_left = 0.0
        pointer_align = "left"
    elif score >= 100.0 - pointer_region_width / 2.0:
        pointer_left = 100.0 - pointer_region_width
        pointer_align = "right"
    else:
        pointer_left = score - pointer_region_width / 2.0
        pointer_align = "center"
    pointer_right = 100.0 - pointer_left - pointer_region_width

    cells = []
    for index in range(20):
        cell_score = index * 5.0
        cell_label = next(
            label for upper, label in VERDICT_THRESHOLDS if cell_score < upper
        )
        cells.append({
            "color": _SENTIMENT_COLOR_BY_LABEL[cell_label][0],
        })

    score_display = f"{score:.1f}".rstrip("0").rstrip(".")
    return {
        "score": score,
        "score_display": score_display,
        # 展示文字与颜色都只信任确定性分数，避免上游 label 漂移后文字/色带矛盾。
        "label": active_label,
        "active_color": _SENTIMENT_COLOR_BY_LABEL[active_label][1],
        "coverage": verdict.get("coverage") if isinstance(verdict.get("coverage"), dict) else None,
        "pointer_percent": score_display,
        "bubble_layout": {
            "left_width": bubble_left,
            "region_width": bubble_region_width,
            "right_width": bubble_right,
            "align": bubble_align,
        },
        "pointer_layout": {
            "left_width": pointer_left,
            "region_width": pointer_region_width,
            "right_width": pointer_right,
            "align": pointer_align,
        },
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


def _filter_rate(value: float | None) -> str:
    """长期隐含收益率不加正号，避免与均线距离的正负语义混淆。"""
    value = _finite_number(value)
    if value is None:
        return "—"
    try:
        return f"{value * 100:.1f}%"
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
    env.filters["rate"] = _filter_rate
    env.filters["metric_num"] = _filter_metric_num
    env.filters["metric_delta"] = _filter_metric_delta
    env.filters["bj_time"] = _filter_bj_time
    env.filters["bj_date_cn"] = _filter_bj_date_cn
    env.filters["iso_date_md"] = _filter_iso_date_md
    env.filters["safe_url"] = _filter_safe_url
    env.filters["cjk_spaced"] = add_cjk_spacing
    env.filters["one_sentence"] = one_sentence_summary
    return env


def _compact_inline_styles(html: str) -> str:
    """压缩双引号 inline style,不改动正文、URL 或 <style> 里的媒体查询。"""

    def _compact(match: re.Match[str]) -> str:
        style = re.sub(r"\s*([:;,])\s*", r"\1", match.group("body").strip())
        style = re.sub(r";+$", "", style)
        return f'style="{style}"'

    return re.sub(r'style="(?P<body>[^"]*)"', _compact, html)


def _compact_oversize_html(html: str) -> tuple[str, bool]:
    """在不破坏正文角标链接的前提下压缩超限邮件。

    邮件客户端自行决定外链如何打开,所以 ``target`` / ``rel`` 对邮件没有实际
    作用,可以先移除。如果仍超限,再移除章节底部与正文角标重复的来源清单；正文
    的蓝色 ``<sup><a href=...>`` 始终保留,避免角标退化为黑色不可点击文本。

    返回 ``(html, source_lists_removed)`` 供日志记录具体采用了哪一级压缩。
    """
    compacted = html.replace(' target="_blank"', "").replace(' rel="noopener"', "")
    if len(compacted.encode("utf-8")) <= _EMAIL_HTML_BUDGET_BYTES:
        return compacted, False
    return _DUPLICATE_SOURCE_LIST_RE.sub("", compacted), True


def render_email(
    *,
    signals: list[StockSignal],
    generated_at: datetime,
    logo_cids: dict[str, str] | None = None,
    # M5 刊头图
    header_image_url: str | None = None,
    # M5 LLM 改写的持仓引言(无值时模板退回原 M2 文案)
    holdings_intro: str | None = None,
    valuations: dict[str, ValuationDisplay] | None = None,
    valuation_checked_at: datetime | None = None,
    # M3 原始数据(始终渲染指标小表 / 错误兜底)
    sentiment: Any | None = None,            # SentimentBundle
    company_news: list[Any] | None = None,    # list[CompanyNewsBundle]
    figures: list[Any] | None = None,         # list[FigureBundle]
    macro_news: list[Any] | None = None,      # list[MacroFeedBundle]
    buffett_13f: Any | None = None,           # BuffettBundle
    jiangsu_fuel_alert: Any | None = None,    # JiangsuFuelAlert（调价前 1—2 天）
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
    valuation_label = (
        "公允价值"
        if valuations
        and any(value.value_label == "公允价值" for value in valuations.values())
        else "内在价值"
    )
    html = template.render(
        signals=signals,
        generated_at=generated_at,
        logo_cids=logo_cids or {},
        header_image_url=header_image_url,
        holdings_intro=holdings_intro,
        valuations=valuations or {},
        valuation_checked_at=valuation_checked_at,
        valuation_label=valuation_label,
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
        jiangsu_fuel_alert=jiangsu_fuel_alert,
        frontier_labs_items=frontier_labs_items or [],
        frontier_labs_fallback_note=frontier_labs_fallback_note,
        judgment_section=judgment_section,
        serif_display=EMAIL_EDITORIAL_SERIF,
        serif_body=EMAIL_EDITORIAL_SERIF,
        serif_quote=EMAIL_EDITORIAL_SERIF,
        numeric_features=EMAIL_NUMERIC_FEATURES,
    )
    # 邮件客户端按解码后的 HTML 体积裁剪。inline style 是模板中
    # 最大的重复项;只压缩属性内 CSS 分隔符与标签间排版空白,不碰正文。
    compacted = _compact_inline_styles(html)
    compacted = re.sub(r"(?<=>)\s+(?=<)", "", compacted).strip()
    size_bytes = len(compacted.encode("utf-8"))
    if size_bytes > _EMAIL_HTML_BUDGET_BYTES:
        original_size = size_bytes
        compacted, source_lists_removed = _compact_oversize_html(compacted)
        size_bytes = len(compacted.encode("utf-8"))
        logger.info(
            "render.email_html_link_preserving_compaction "
            "before=%d after=%d budget=%d source_lists_removed=%s",
            original_size,
            size_bytes,
            _EMAIL_HTML_BUDGET_BYTES,
            source_lists_removed,
        )
    if size_bytes > _EMAIL_HTML_BUDGET_BYTES:
        logger.warning(
            "render.email_html_oversize bytes=%d budget=%d",
            size_bytes,
            _EMAIL_HTML_BUDGET_BYTES,
        )
    return compacted
