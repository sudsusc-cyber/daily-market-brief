"""
本地预览邮件模板渲染效果。

不调用 yfinance,直接构造覆盖 DCA / LUMP_SUM / NONE / error / 含 logo / 缺 logo
等各种状态的样例数据,渲染到 /tmp/email_preview.html。

cid: 在浏览器里无法解析,所以预览时把所有 cid: 引用替换为 base64 data URI。
真实邮件发送(src/main.py)走 SMTP multipart/related,保留原始 cid: 形式。

用法:
    uv run python scripts/preview_email.py
"""

from __future__ import annotations

import base64
import sys
import tempfile
import webbrowser
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


from src.collectors.stocks import StockSignal  # noqa: E402
from src.config import HOLDINGS, Holding  # noqa: E402
from src.utils.email_typography import EMAIL_EDITORIAL_SERIF  # noqa: E402
from src.valuation.models import ValuationDisplay  # noqa: E402

LOGOS_DIR = PROJECT_ROOT / "assets" / "logos"


def _format_price(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:,.2f}"


def _format_pct(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v * 100:+.1f}%"


def _build_mock_signals() -> list[StockSignal]:
    """
    构造 14 只样例数据,与真实持仓 ticker 一致以复用 logo 文件。
    默认全部 OK,包含 DCA / LUMP_SUM / NONE 三种信号 + BRK.B 的"无 logo 文字 fallback"。
    错误态视觉若需复核,临时把任一行最后一个字段填字符串错误信息即可。
    """
    cases: list[tuple[Holding, float | None, float | None, float | None, str | None]] = [
        (HOLDINGS[0], 432.10, 410.50, 360.20, None),   # MSFT, NONE
        (HOLDINGS[1], 968.45, 875.20, 720.40, None),   # COST, NONE
        (HOLDINGS[2], 232.80, 215.60, 188.10, None),   # AAPL, NONE
        (HOLDINGS[3], 209.25, 140.29, 96.22, None),    # NVDA, NONE
        (HOLDINGS[4], 175.80, 168.20, 132.40, None),   # TSM, NONE
        (HOLDINGS[5], 478.30, 502.80, 390.50, None),   # MCO, DCA
        (HOLDINGS[6], 198.40, 185.60, 152.30, None),   # GOOG, NONE
        (HOLDINGS[7], 462.10, 451.20, 388.40, None),   # BRK.B, NONE(无 logo,走文字 fallback)
        (HOLDINGS[8], 64.20, 68.40, 60.10, None),      # KO, DCA
        (HOLDINGS[9], 268.90, 295.40, 312.80, None),   # AXP, LUMP_SUM
        (HOLDINGS[10], 412.40, 380.60, 340.20, None),  # 0700.HK, NONE
        (HOLDINGS[11], 157.10, 136.01, 89.67, None),   # 9992.HK 泡泡玛特, NONE
        (HOLDINGS[12], 512.30, 478.20, 412.60, None),  # MA 万事达, NONE
        (HOLDINGS[13], 445.80, 462.10, 398.40, None),  # LIN 林德, DCA
    ]

    signals: list[StockSignal] = []
    for holding, last, sma120, sma200, err in cases:
        if err:
            signals.append(StockSignal(holding, None, None, None, None, None, "NONE", error=err))
            continue
        if last is None or sma120 is None or sma200 is None:
            raise ValueError(f"invalid preview fixture for {holding.ticker}")
        delta_120 = (last - sma120) / sma120
        delta_200 = (last - sma200) / sma200
        if last <= sma200:
            sig = "LUMP_SUM"
        elif last <= sma120:
            sig = "DCA"
        else:
            sig = "NONE"
        signals.append(StockSignal(holding, last, sma120, sma200, delta_120, delta_200, sig))
    return signals


def _logo_path(h: Holding):
    """按优先级查找 png/jpg/jpeg 文件,返回首个存在的 Path 或 None。"""
    for ext in ("png", "jpg", "jpeg"):
        p = LOGOS_DIR / f"{h.slug}.{ext}"
        if p.exists():
            return p
    return None


def _hashed_cid(h: Holding) -> str | None:
    """与 main.py._load_logo_assets 同款 CID:logo_<slug>_<sha8>。"""
    import hashlib
    p = _logo_path(h)
    if p is None:
        return None
    sha8 = hashlib.sha1(p.read_bytes(), usedforsecurity=False).hexdigest()[:8]
    return f"{h.logo_cid}_{sha8}"


def _build_logo_cids() -> dict[str, str]:
    """ticker -> 带 hash 的 CID;只对 assets/logos/<slug>.{png,jpg,jpeg} 实际存在的入字典"""
    out: dict[str, str] = {}
    for h in HOLDINGS:
        cid = _hashed_cid(h)
        if cid:
            out[h.ticker] = cid
    return out


def _build_mock_valuations() -> dict[str, ValuationDisplay]:
    """仅供视觉预览；数值取本次公开 Morningstar 全量复核，不写入生产底稿。"""
    values = {
        "MSFT": (600.00, "$"),
        "COST": (650.00, "$"),
        "AAPL": (285.00, "$"),
        "NVDA": (280.00, "$"),
        "TSM": (534.00, "$"),
        "MCO": (500.00, "$"),
        "GOOG": (433.00, "$"),
        "BRK.B": (510.00, "$"),
        "KO": (74.00, "$"),
        "AXP": (335.00, "$"),
        "0700.HK": (800.00, "HK$"),
        "9992.HK": (280.00, "HK$"),
        "MA": (550.00, "$"),
        "LIN": (540.00, "$"),
    }
    prices = {signal.holding.ticker: signal.last_close for signal in _build_mock_signals()}
    return {
        ticker: ValuationDisplay(
            ticker=ticker,
            status="current",
            intrinsic_value=value,
            implied_return=value / prices[ticker] - 1,
            hurdle_rate=0.10,
            currency_symbol=symbol,
            return_label="1Y IRR",
            value_label="公允价值",
            financial_as_of="2026-Q2",
            approved_at="2026-08-28",
        )
        for ticker, (value, symbol) in values.items()
    }


def _inline_logos_as_data_uri(html: str) -> str:
    """把 <img src='cid:logo_XXX_<hash>'> 替换为 data:image/...;base64,..., 便于浏览器预览。"""
    from src.sender.smtp_sender import _detect_image_subtype  # 复用 sender 的检测

    for h in HOLDINGS:
        path = _logo_path(h)
        cid = _hashed_cid(h)
        if path is None or cid is None:
            continue
        data = path.read_bytes()
        subtype = _detect_image_subtype(data) or "png"
        b64 = base64.b64encode(data).decode("ascii")
        data_uri = f"data:image/{subtype};base64,{b64}"
        html = html.replace(f"cid:{cid}", data_uri)
    return html


def render_preview(*, inline_assets: bool = True) -> str:
    import datetime as dt
    from types import SimpleNamespace

    from src.collectors.header_image import pick_header_image
    header = pick_header_image(dt.date.today())
    header_url = header["url"]
    # cid: 在浏览器无法加载,换用固定 Pexels URL 供预览
    if header_url.startswith("cid:"):
        header_url = (
            "https://images.pexels.com/photos/691668/pexels-photo-691668.jpeg"
            "?auto=compress&cs=tinysrgb&w=1280&h=640&fit=crop"
        )

    from src.renderer.render import render_email

    mock_sentiment = SimpleNamespace(metrics=[
        SimpleNamespace(name="CNN Fear & Greed", unit="", stale_from=None, error=None,
                        current=67.5, prior=64.0, delta=3.5),
        SimpleNamespace(name="VIX", unit="", stale_from=None, error=None,
                        current=20.0, prior=20.4, delta=-0.4),
        SimpleNamespace(name="高收益债利差", unit="%", stale_from=None, error=None,
                        current=4.0, prior=4.1, delta=-0.1),
        SimpleNamespace(name="Shiller PE", unit="", stale_from=None, error=None,
                        current=25.0, prior=25.0, delta=0.0),
        SimpleNamespace(name="DXY", unit="", stale_from=None, error=None,
                        current=100.0, prior=100.2, delta=-0.2),
    ])
    mock_sentiment_verdict = {
        "verdict": "今日情绪 · 偏热",
        "argument": "CNN 恐惧贪婪指数回升且 VIX 保持低位，风险偏好偏热，暂缓加仓并守住现金仓位。",
        "score": 67.5,
    }

    # mock 昨日动态:用 news_summarizer 的真实渲染逻辑跑一段
    from src.processors.news_summarizer import CompanyNewsSummary, Footnote
    mock_lines = [
        "<strong>苹果</strong> —— App Store 抽成案被驳回,案件移交最高法院。",
        "<strong>英伟达</strong> —— Arrive AI 部署 Isaac Sim 与 Blackwell GPU 用于机器人视觉训练。",
        "<strong>泡泡玛特</strong> —— LABUBU 冰箱开售秒罄,二手市场溢价 4000 元。",
        "<strong>腾讯</strong> —— 4 月获 154 款游戏版号,开源轻量端侧翻译模型。",
    ]
    name_style = "color:#7A1F2B; letter-spacing:0.04em;"
    sep_style = "color:#D9D2BE; margin:0 6px;"
    row_style = (
        "margin:0 0 10px 0; padding:0;"
        f"font-family:{EMAIL_EDITORIAL_SERIF};"
        "font-size:16px; line-height:1.9; color:#1A1A1A; letter-spacing:0.02em;"
    )
    body_html = ""
    import re as _re
    row_re = _re.compile(r"<strong>(.+?)</strong>\s*[——\-:、]+\s*(.+)")
    for line in mock_lines:
        m = row_re.match(line)
        if m:
            cn, summary = m.group(1).strip(), m.group(2).strip()
            body_html += (
                f'<div style="{row_style}">'
                f'<span style="{name_style}">{cn}</span>'
                f'<span style="{sep_style}">│</span>'
                f'{summary}</div>'
            )
    mock_summary = CompanyNewsSummary(
        summary_html=body_html,
        footnotes=[Footnote(index=1, url='https://example.com/1', source='新浪财经'),
                   Footnote(index=2, url='https://example.com/2', source='Reuters')],
    )

    html = render_email(
        signals=_build_mock_signals(),
        generated_at=datetime.now(ZoneInfo("Asia/Shanghai")),
        logo_cids=_build_logo_cids(),
        header_image_url=header_url,
        valuations=_build_mock_valuations(),
        valuation_checked_at=datetime.now(ZoneInfo("Asia/Shanghai")),
        # company_news 只需 truthy(jinja2 if 检查),company_news_summary 提供真实 mock
        sentiment=mock_sentiment,
        sentiment_verdict=mock_sentiment_verdict,
        company_news=[1], company_news_summary=mock_summary,
        figures=None, figure_summaries=None,
        macro_news=None, macro_news_summary=None,
        buffett_13f=None,
    )
    return _inline_logos_as_data_uri(html) if inline_assets else html


def main() -> int:
    raw_html = render_preview(inline_assets=False)
    html = render_preview(inline_assets=True)
    out = Path(tempfile.gettempdir()) / "email_preview.html"
    out.write_text(html, encoding="utf-8")
    print(
        f"已写入 {out} (真实邮件 HTML {len(raw_html.encode('utf-8')):,} bytes; "
        f"浏览器内嵌资源后 {len(html.encode('utf-8')):,} bytes)"
    )
    print(f"file://{out}")
    try:
        webbrowser.open(f"file://{out}")
    except Exception as exc:  # noqa: BLE001
        print(f"未能自动打开浏览器: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
