"""
邮件 HTML 渲染入口。

输入:
  - signals: list[StockSignal]
  - generated_at: datetime(应该是 Asia/Shanghai)
  - 后续 M3 起会有 sentiment / company_news / figures / macro_news

输出:
  - HTML 字符串(完整的 <!doctype html>...</html>)

模板位置:src/renderer/templates/email.html.j2
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.collectors.stocks import StockSignal

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


def _build_env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(_TEMPLATE_DIR),
        autoescape=select_autoescape(["html"]),
        trim_blocks=False,
        lstrip_blocks=False,
    )
    env.filters["price"] = _filter_price
    env.filters["pct"] = _filter_pct
    return env


def render_email(
    *,
    signals: list[StockSignal],
    generated_at: datetime,
    sentiment: dict | None = None,
    company_news: str | None = None,
    figures: list | None = None,
    macro_news: str | None = None,
) -> str:
    """渲染完整邮件 HTML"""
    env = _build_env()
    template = env.get_template("email.html.j2")
    return template.render(
        signals=signals,
        generated_at=generated_at,
        sentiment=sentiment,
        company_news=company_news,
        figures=figures,
        macro_news=macro_news,
    )
