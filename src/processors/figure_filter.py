"""
关键人物发言筛选与摘要(模块 3a/3b 加工)。

输入:list[FigureBundle](已经过 M3 第一道规则筛选 + 7 天 dedupe)
任务:第二道 LLM 筛选 + 关键观点提炼,产出可在邮件 IV 区块直接呈现的 1-3 句中文。

输出:list[FigureSummary](人物 → 中文摘要 list,可空)
失败时返回原 bundles 的轻量映射(直接 fall back 到原候选列表展示)。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from src.collectors.figures import FigureBundle, FigureMention
from src.processors.llm_client import LLMClient

logger = logging.getLogger(__name__)


@dataclass
class FigureKeyPoint:
    """单条 LLM 提炼后的关键观点"""
    text: str  # 中文摘要,1-2 句
    source_url: str  # 原报道链接
    source_name: str  # 媒体名


@dataclass
class FigureSummary:
    """单个人物的加工产物"""
    person: str
    items: list[FigureKeyPoint] = field(default_factory=list)
    fallback_raw: list[FigureMention] = field(default_factory=list)  # LLM 失败时模板用
    error: str | None = None


_TASK_INSTRUCTION = """\
任务:对下面"人物的候选发言列表"做两件事:
1. 判断每条是否本人原话/演讲/采访/正式声明(而非他人转述其旧话或评论他)
2. 对判定为"本人原话"的条目,提炼 1-2 句中文关键观点(去掉新闻标题的标题党语气)

输出严格按以下行格式,每行对应一条候选(按输入顺序),不要解释、不要前言:
▦ N: yes | <关键观点中文>
▦ N: no  | <一句话说明为什么不是本人原话>

要求:
- "关键观点"要忠实原文,不引申、不解读
- 若标题里就含直接引语(""),优先保留引语原意
- 输出仅这些行;输出行数必须等于输入条目数
"""


_LINE_RE = re.compile(r"^▦\s*(\d+)\s*:\s*(yes|no)\s*\|\s*(.+?)\s*$", re.IGNORECASE)


def _format_input(items: list[FigureMention]) -> str:
    lines: list[str] = []
    for i, it in enumerate(items, start=1):
        snippet = (it.snippet or "").strip()
        # 摘要太长会污染 prompt,裁到 200 字
        if len(snippet) > 200:
            snippet = snippet[:200].rstrip() + "…"
        line = f"▦ {i}: 标题={it.title}"
        if snippet:
            line += f" / 摘要={snippet}"
        line += f" / 来源={it.source}"
        lines.append(line)
    return "\n".join(lines)


def _parse_output(text: str, items: list[FigureMention]) -> list[FigureKeyPoint]:
    kept: list[FigureKeyPoint] = []
    for line in text.splitlines():
        m = _LINE_RE.match(line.strip())
        if not m:
            continue
        try:
            idx = int(m.group(1))
        except ValueError:
            continue
        verdict = m.group(2).lower()
        body = m.group(3).strip()
        if verdict != "yes":
            continue
        if not (1 <= idx <= len(items)):
            continue
        src_item = items[idx - 1]
        kept.append(FigureKeyPoint(
            text=body,
            source_url=src_item.url,
            source_name=src_item.source,
        ))
    return kept


def filter_one(bundle: FigureBundle, *, client: LLMClient, max_items: int = 5) -> FigureSummary:
    """加工单个人物"""
    if bundle.error or not bundle.items:
        return FigureSummary(
            person=bundle.person,
            fallback_raw=bundle.items[:max_items],
            error=bundle.error,
        )
    feed_items = bundle.items[:max_items]
    payload = _format_input(feed_items)
    resp = client.chat(
        payload,
        task_extra=_TASK_INSTRUCTION.replace("人物", bundle.person),
        max_tokens=900,
        temperature=0.2,
    )
    if not resp.text:
        logger.warning("figure_filter.failed person=%s reason=%s", bundle.person, resp.error)
        return FigureSummary(
            person=bundle.person,
            fallback_raw=feed_items,
            error=resp.error,
        )
    kept = _parse_output(resp.text, feed_items)
    logger.info("figure_filter.ok person=%s in=%d kept=%d",
                bundle.person, len(feed_items), len(kept))
    return FigureSummary(
        person=bundle.person,
        items=kept,
        fallback_raw=feed_items if not kept else [],  # LLM 全 no 则展示原始候选
    )


def filter_all(
    bundles: list[FigureBundle], *, client: LLMClient, max_items: int = 5
) -> list[FigureSummary]:
    return [filter_one(b, client=client, max_items=max_items) for b in bundles]
