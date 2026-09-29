"""Source-derived position introduction; no generated market claims."""
from __future__ import annotations

from typing import Any

from src.processors.llm_client import LLMClient


def _format_input(signals: list[Any]) -> str:
    """把 list[StockSignal] 序列化为 LLM 可读的简表。"""
    n_lump = sum(1 for s in signals if s.signal == "LUMP_SUM")
    n_dca = sum(1 for s in signals if s.signal == "DCA")
    n_none = sum(1 for s in signals if s.signal == "NONE")
    n_err = sum(1 for s in signals if s.error)

    lines = [f"信号汇总:LUMP_SUM={n_lump},DCA={n_dca},无信号={n_none},取数失败={n_err}"]
    lines.append("")
    lines.append("逐只明细(ticker | 现价 | 参考线与偏离度 | 信号；双线依次为 DCA/大额，单线仅大额):")
    for s in signals:
        if s.error:
            lines.append(f"- {s.holding.ticker} | 错误:{s.error[:40]}")
            continue
        line_text = " / ".join(
            f"{line['label']}:{line['delta'] * 100:+.1f}%"
            if line["delta"] is not None else f"{line['label']}:—"
            for line in s.buy_lines
        )
        lines.append(
            f"- {s.holding.ticker} | {s.last_close:.2f} | {line_text} | {s.signal}"
        )
    return "\n".join(lines)


def write_intro(signals: list[Any], *, client: LLMClient | None = None) -> str | None:
    """Describe signal states only, never infer price/market facts from NONE.

    Keep the client keyword for callers; this small publication block needs no
    model. Known prose plus actual counts cannot invent geography or buy lines.
    """
    if not signals:
        return None
    counts = {key: 0 for key in ("DCA", "LUMP_SUM", "NONE")}
    pending = 0
    for signal in signals:
        if signal.error or signal.signal not in counts:
            pending += 1
        else:
            counts[signal.signal] += 1
    parts = []
    if counts["DCA"]:
        parts.append(f"{counts['DCA']}只处于小额区间")
    if counts["LUMP_SUM"]:
        parts.append(f"{counts['LUMP_SUM']}只处于大额区间")
    if counts["NONE"]:
        parts.append(f"{counts['NONE']}只暂无买入信号")
    if pending:
        parts.append(f"{pending}只信号待核验")
    return "本期持仓中，" + "，".join(parts) + "。潮水自有起落，守候不必追逐每一道浪。"
