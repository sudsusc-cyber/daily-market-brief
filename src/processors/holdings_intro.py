"""
持仓引言改写(M5 加工)。

把固定的、教科书式的持仓数量说明段落,
改写为简短、有哲学意味、文笔讲究的开场白(60-110 字,prompt 内硬约束)。

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

_MAX_ATTEMPTS = 2


_TASK_INSTRUCTION = """\
任务:基于今日持仓的实际信号与偏离度,写**一段富有哲学韵味、文采斐然、发人深省**的中文开场白
(60-110 字,1-2 句),作为晨报「持仓信号」章节的首句。

【风格基调(必读)】
- 取法 Berkshire 致股东信 / Howard Marks《Memo to Oaktree Clients》/
  芒格《穷查理宝典》—— 那种节制、洗练、有古典文气、读完让人回味的语调
- 可以化用古典意象、诗句意境、东西方哲学命题(如"潮水退去""时间的复利""逆水行舟"
  "祸福相倚""大智若愚""曲突徙薪"等),但**不要直接引用名句**
- 必须**与今日具体持仓信号有自然关联**——例如全是 NONE 的安静日子可写"喧嚣之外"的克制;
  出现 DCA/Lump-sum 时可写"风起于青萍之末"或"折扣即馈赠"的耐心;价格普遍高于均线时
  可写"潮高时不忘退潮"的清醒
- 文采要"雅而不晦",一句话能让读者停下来思考一秒,而不是空泛的鸡汤

【硬约束】
- 只输出**那段开场白本身**的中文文字,不带前言、不带解释、不带 markdown、不带引号、不分点
- 字数 60-110 字之间,1-2 句最佳;**绝不超过 110 字**
- **不要 AI 腔**:不写"今日""让我们""在当前市场环境下""值得我们关注"
- **不要重复**持仓总数、美股/港股数量这类结构性陈述(模板会动态生成)
- **不要劝告**读者具体怎么操作("应该买入""建议加仓"全禁)
- **不要堆砌**"耐心""纪律""安全边际"这些词;要让读者**感受到**这些品质而非看到这些字
- 必须暗合今日实际:全 NONE / 多个 DCA / 多个 LUMP_SUM / 价格普遍高位 vs 普遍低位
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
    text = ""
    last_error: str | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        resp = client.chat(
            payload,
            task_extra=_TASK_INSTRUCTION,
            max_tokens=800,
            temperature=0.7,
            timeout=20,
            thinking=False,
        )
        text = (resp.text or "").strip()
        if text:
            break
        last_error = resp.error or "EmptyOutput"
        logger.warning(
            "holdings_intro.attempt_failed attempt=%d/%d reason=%s",
            attempt, _MAX_ATTEMPTS, last_error,
        )
    if not text:
        logger.warning("holdings_intro.failed reason=%s", last_error or "unknown")
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
