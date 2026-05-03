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

from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.collectors.stocks import StockSignal
from src.processors.html_safe import is_safe_url
from src.renderer.text_utils import add_cjk_spacing
from src.utils.dates import to_beijing

_TEMPLATE_DIR = Path(__file__).parent / "templates"


def _filter_price(value: float | None) -> str:
    """浮点 → 千分位 + 2 位小数;None 或 NaN 返回长破折号"""
    if value is None:
        return "—"
    try:
        return f"{value:,.2f}"
    except (TypeError, ValueError):
        return "—"


def _filter_pct(value: float | None) -> str:
    """浮点(0.092 形式)→ '+9.2%'/'-9.2%';None 返回长破折号"""
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
) -> str:
    """
    渲染完整邮件 HTML。

    logo_cids: ticker -> CID 映射,如 {"NVDA": "logo_NVDA"}。
    M4 加工产物若为 None,模板降级渲染 M3 原始数据列表。
    """
    env = _build_env()
    template = env.get_template("email.html.j2")
    return template.render(
        signals=signals,
        generated_at=generated_at,
        logo_cids=logo_cids or {},
        header_image_url=header_image_url,
        holdings_intro=holdings_intro,
        sentiment=sentiment,
        sentiment_verdict=sentiment_verdict,
        company_news=company_news,
        company_news_summary=company_news_summary,
        figures=figures,
        figure_summaries=figure_summaries,
        figure_silence_note=figure_silence_note,
        figure_footnotes=figure_footnotes or [],
        macro_news=macro_news,
        macro_news_summary=macro_news_summary,
        buffett_13f=buffett_13f,
    )
