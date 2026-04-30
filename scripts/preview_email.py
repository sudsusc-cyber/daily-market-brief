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
import webbrowser
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from jinja2 import Environment, FileSystemLoader, select_autoescape  # noqa: E402

from src.collectors.stocks import StockSignal  # noqa: E402
from src.config import HOLDINGS, Holding  # noqa: E402

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
    构造 12 只样例数据,与真实持仓 ticker 一致以复用 logo 文件。
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
    ]

    signals: list[StockSignal] = []
    for holding, last, sma120, sma200, err in cases:
        if err:
            signals.append(StockSignal(holding, None, None, None, None, None, "NONE", error=err))
            continue
        assert last is not None and sma120 is not None and sma200 is not None
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


def _build_logo_cids() -> dict[str, str]:
    """ticker -> CID;只对 assets/logos/<slug>.png 实际存在的入字典"""
    cids: dict[str, str] = {}
    for h in HOLDINGS:
        if (LOGOS_DIR / f"{h.slug}.png").exists():
            cids[h.ticker] = h.logo_cid
    return cids


def _inline_logos_as_data_uri(html: str) -> str:
    """把 <img src='cid:logo_XXX'> 替换为 data:image/...;base64,..., 便于浏览器预览。
    按 magic bytes 推断 MIME 类型(可能是 png / jpeg / svg)。"""
    from src.sender.smtp_sender import _detect_image_subtype  # 复用 sender 的检测

    for h in HOLDINGS:
        path = LOGOS_DIR / f"{h.slug}.png"
        if not path.exists():
            continue
        data = path.read_bytes()
        subtype = _detect_image_subtype(data) or "png"
        b64 = base64.b64encode(data).decode("ascii")
        data_uri = f"data:image/{subtype};base64,{b64}"
        html = html.replace(f"cid:{h.logo_cid}", data_uri)
    return html


def render_preview() -> str:
    env = Environment(
        loader=FileSystemLoader(PROJECT_ROOT / "src" / "renderer" / "templates"),
        autoescape=select_autoescape(["html"]),
    )
    env.filters["price"] = _format_price
    env.filters["pct"] = _format_pct
    template = env.get_template("email.html.j2")
    html = template.render(
        signals=_build_mock_signals(),
        generated_at=datetime.now(ZoneInfo("Asia/Shanghai")),
        logo_cids=_build_logo_cids(),
    )
    return _inline_logos_as_data_uri(html)


def main() -> int:
    html = render_preview()
    out = Path("/tmp/email_preview.html")
    out.write_text(html, encoding="utf-8")
    print(f"已写入 {out} ({len(html):,} bytes)")
    print(f"file://{out}")
    try:
        webbrowser.open(f"file://{out}")
    except Exception as exc:  # noqa: BLE001
        print(f"未能自动打开浏览器: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
