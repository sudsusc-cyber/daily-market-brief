"""
持仓引言改写(M5 加工)。

把固定的、教科书式的"当前持仓十二只——美股十只,港股两只……"段落,
改写为简短、有哲学意味、文笔讲究的开场白(80-130 字)。

设计要点:
- 输入是当日全部 StockSignal(含 ticker/last_close/sma_120/sma_200/signal/error)
- 输出是单段中文,直接渲染到模板
- 行文基调:Berkshire 致股东信 / Howard Marks Memo / 巴菲特价值投资框架
- 失败时返回 None,模板降级到原文案
"""

from __future__ import annotations

import logging
from typing import Any

from src.processors.llm_client import LLMClient

logger = logging.getLogger(__name__)


_TASK_INSTRUCTION = """\
任务:基于今日持仓信号,写**一段中文开场白**(80-130 字,1-2 句),用于晨报「持仓信号」章节首句。

风格基调(必读):
- Berkshire 致股东信 + Howard Marks《Memo to Oaktree Clients》+ 巴菲特价值投资框架
- 文笔克制、含蓄、结实,有哲学意味与时代洞察
- 写出"持续观察、纪律、耐心、安全边际"等价值投资精神,但**不要直接堆砌这些词**
- 用克制的现代汉语,避免口号、避免劝告读者怎么做、避免 AI 腔(如"今日"、"让我们"、"在当前市场环境下")
- 1-2 句,行云流水,不要并列短句堆砌

输出约束:
- 只输出**那段开场白本身**的中文文字,不带前言、不带解释、不带 markdown、不带引号
- 不要分点、不要列表、不要副标题
- 不要重复"持仓十二只""美股十只港股两只"这种结构性陈述——这是模板已有信息
- 必须暗合今日的实际信号分布(全部观望 vs 出现 DCA vs 出现 Lump-sum)与价格-均线偏离的整体味道
- 简短优美,胜过冗长描述
"""


def _format_input(signals: list[Any]) -> str:
    """把 list[StockSignal] 序列化为 LLM 可读的简表。"""
    n_lump = sum(1 for s in signals if s.signal == "LUMP_SUM")
    n_dca = sum(1 for s in signals if s.signal == "DCA")
    n_none = sum(1 for s in signals if s.signal == "NONE")
    n_err = sum(1 for s in signals if s.error)

    lines = [f"信号汇总:LUMP_SUM={n_lump},DCA={n_dca},无信号={n_none},取数失败={n_err}"]
    lines.append("")
    lines.append("逐只明细(ticker | 现价 | 120w | 200w | 信号):")
    for s in signals:
        if s.error:
            lines.append(f"- {s.holding.ticker} | 错误:{s.error[:40]}")
            continue
        d120 = "—" if s.delta_120 is None else f"{s.delta_120 * 100:+.1f}%"
        d200 = "—" if s.delta_200 is None else f"{s.delta_200 * 100:+.1f}%"
        lines.append(
            f"- {s.holding.ticker} | {s.last_close:.2f} | {d120} | {d200} | {s.signal}"
        )
    return "\n".join(lines)


def write_intro(signals: list[Any], *, client: LLMClient) -> str | None:
    """成功返回开场白字符串(80-130 字),失败返回 None。"""
    if not signals:
        return None
    payload = _format_input(signals)
    resp = client.chat(
        payload,
        task_extra=_TASK_INSTRUCTION,
        max_tokens=600,
        temperature=0.7,
    )
    text = (resp.text or "").strip()
    if not text:
        logger.warning("holdings_intro.failed reason=%s", resp.error)
        return None
    # 去除偶发的引号包裹与 markdown
    text = text.strip("\"'“”「」 ").strip()
    if text.startswith("```"):
        text = text.strip("` \n")
    # 极端长度兜底:模型可能溢出,截到约 200 字
    if len(text) > 200:
        text = text[:200].rstrip("。!?,;:") + "。"
    logger.info("holdings_intro.ok chars=%d", len(text))
    return text
