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
    """情绪指标当前值/一周前值,带单位。None → '—'"""
    if value is None:
        return "—"
    try:
        # 数字大小决定保留位数
        absv = abs(value)
        if absv >= 100:
            text = f"{value:,.1f}"
        elif absv >= 10:
            text = f"{value:.2f}"
        else:
            text = f"{value:.2f}"
    except (TypeError, ValueError):
        return "—"
    return f"{text}{unit}" if unit else text


def _filter_metric_delta(delta: float | None, unit: str = "") -> str:
    """情绪指标变化值,带正负号。None → '—'"""
    if delta is None:
        return "—"
    try:
        absv = abs(delta)
        if absv >= 100:
            text = f"{delta:+,.1f}"
        else:
            text = f"{delta:+.2f}"
    except (TypeError, ValueError):
        return "—"
    return f"{text}{unit}" if unit else text


def _filter_bj_time(dt: datetime | None) -> str:
    """datetime → 北京时间 'MM-DD HH:MM' 字符串"""
    if dt is None:
        return ""
    try:
        return to_beijing(dt).strftime("%m-%d %H:%M")
    except Exception:  # noqa: BLE001
        return ""


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
    return env


def render_email(
    *,
    signals: list[StockSignal],
    generated_at: datetime,
    logo_cids: dict[str, str] | None = None,
    # M3 原始数据(始终渲染指标小表 / 错误兜底)
    sentiment: Any | None = None,            # SentimentBundle
    company_news: list[Any] | None = None,    # list[CompanyNewsBundle]
    figures: list[Any] | None = None,         # list[FigureBundle]
    macro_news: list[Any] | None = None,      # list[MacroFeedBundle]
    buffett_13f: Any | None = None,           # BuffettBundle
    # M4 LLM 加工产物(若为 None,模板自动降级到 M3 原始数据)
    sentiment_verdict: dict | None = None,           # {verdict, argument}
    company_news_paragraph: str | None = None,       # 200-400 字段落
    figure_summaries: list[Any] | None = None,        # list[FigureSummary]
    macro_news_paragraph: str | None = None,         # 150-300 字段落
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
        sentiment=sentiment,
        sentiment_verdict=sentiment_verdict,
        company_news=company_news,
        company_news_paragraph=company_news_paragraph,
        figures=figures,
        figure_summaries=figure_summaries,
        macro_news=macro_news,
        macro_news_paragraph=macro_news_paragraph,
        buffett_13f=buffett_13f,
    )
