"""
本地预览邮件模板渲染效果。

不调用 yfinance,直接构造覆盖 DCA / LUMP_SUM / NONE / error 各种状态的样例数据,
渲染到 /tmp/email_preview.html 并尝试打开浏览器。

用法:
    uv run python scripts/preview_email.py
"""

from __future__ import annotations

import sys
import webbrowser
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from jinja2 import Environment, FileSystemLoader, select_autoescape  # noqa: E402

from src.collectors.stocks import StockSignal  # noqa: E402
from src.config import Holding  # noqa: E402


def _format_price(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:,.2f}"


def _format_pct(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v * 100:+.1f}%"


def _build_mock_signals() -> list[StockSignal]:
    """构造覆盖各种状态的 12 只样例数据"""
    cases: list[tuple[Holding, float | None, float | None, float | None, str | None]] = [
        (Holding("MSFT", "Microsoft"), 432.10, 410.50, 360.20, None),
        (Holding("COST", "Costco Wholesale"), 968.45, 875.20, 720.40, None),
        (Holding("AAPL", "Apple"), 232.80, 215.60, 188.10, None),
        (Holding("NVDA", "NVIDIA"), 209.25, 140.29, 96.22, None),
        (Holding("TSM", "Taiwan Semiconductor (ADR)"), 175.80, 168.20, 132.40, None),
        (Holding("MCO", "Moody's"), 478.30, 502.80, 390.50, None),  # DCA
        (Holding("GOOG", "Alphabet C"), 198.40, 185.60, 152.30, None),
        (Holding("BRK.B", "Berkshire Hathaway B"), 462.10, 451.20, 388.40, None),
        (Holding("KO", "The Coca-Cola Company"), 64.20, 68.40, 60.10, None),  # DCA
        (Holding("AXP", "American Express"), 268.90, 295.40, 312.80, None),  # LUMP_SUM
        (Holding("0700.HK", "腾讯控股"), 412.40, 380.60, 340.20, None),
        (Holding("9992.HK", "泡泡玛特"), None, None, None, "yfinance 返回空数据(临时不可达)"),
    ]

    signals: list[StockSignal] = []
    for holding, last, sma120, sma200, err in cases:
        if err:
            signals.append(
                StockSignal(holding, None, None, None, None, None, "NONE", error=err)
            )
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


def main() -> int:
    env = Environment(
        loader=FileSystemLoader(PROJECT_ROOT / "src" / "renderer" / "templates"),
        autoescape=select_autoescape(["html"]),
        trim_blocks=False,
        lstrip_blocks=False,
    )
    env.filters["price"] = _format_price
    env.filters["pct"] = _format_pct

    template = env.get_template("email.html.j2")
    html = template.render(
        signals=_build_mock_signals(),
        generated_at=datetime.now(ZoneInfo("Asia/Shanghai")),
    )

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
