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
任务:对下面"人物的候选发言列表"做三件事:
1. 判断每条是否本人原话/演讲/采访/正式声明(而非他人转述其旧话或评论他)
2. **去重合并**:多条 yes 若讲的是同一件事/同一个观点(常因不同媒体报道同场演讲),合并为一条
3. 对判定为"本人原话"且去重后的条目,提炼 1-2 句中文关键观点(去掉标题党语气)

输出严格按以下行格式,不要解释、不要前言:
▦ N: yes | <关键观点中文>             # 单条
▦ N,M[,K]: yes | <关键观点中文>       # M、K 与 N 是同一件事,合并后取 N 为代表来源
▦ N: no  | <一句话说明为什么不是本人原话>

【关键要求】
- **绝对不要在观点开头加 "黄仁勋说" / "巴菲特表示" / "但斌认为" 等人名前缀**
  (人物姓名已作为小标题在上方,正文重复人名很啰嗦)
- 直接输出观点本身,如:"AI infrastructure 投入仍在早期阶段,推理需求增长远超预期"
- 忠实原文,不引申、不解读
- 若标题含直接引语(""),优先保留引语原意

【去重合并规则(重要!)】
- 同一场演讲/采访被多家媒体报道 → 合并(写为 "1,3,5: yes | ...")
- 不同场合但表达相同核心观点 → 合并
- 不同观点(即便人物相同)→ **保持独立条目**,不要硬凑
- 合并时主索引(N)取**信息最完整、来源最权威**的那条;其它索引按递增顺序写在逗号后
- 输出去重后,每条观点都应该是**独立、不重复**的;读者不应看到两条说同一件事

【no 行要求】
- 一句话说明:为什么不算本人原话(他人转述、市场评论、二次解读、广告软文等)
- no 行内容不会展示给读者,只用于让我们看到 LLM 的判断依据

【输出行数】
- yes 行总覆盖的输入索引数 + no 行索引数 = 输入条目数(每个输入索引必须出现在某行中,只一次)
- 不要把同一索引写到多行
"""


# 索引可以是单个(`1`)或逗号分隔合并(`1,3,5`),取第一个为代表来源
_LINE_RE = re.compile(r"^▦\s*([\d,\s]+?)\s*:\s*(yes|no)\s*\|\s*(.+?)\s*$", re.IGNORECASE)


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
    seen_texts: set[str] = set()  # 二重保险:防止 LLM 误重复输出同一观点
    for line in text.splitlines():
        m = _LINE_RE.match(line.strip())
        if not m:
            continue
        idx_part = m.group(1).strip()
        verdict = m.group(2).lower()
        body = m.group(3).strip()
        if verdict != "yes":
            continue
        # 解析索引(可能是 "1" 或 "1,3,5"),取第一个有效的为代表来源
        primary_idx: int | None = None
        for tok in idx_part.split(","):
            try:
                v = int(tok.strip())
                if 1 <= v <= len(items):
                    primary_idx = v
                    break
            except ValueError:
                continue
        if primary_idx is None:
            continue
        # 文本归一化去重:相同关键观点只保留一条
        norm = re.sub(r"\s+", "", body)
        if norm in seen_texts:
            continue
        seen_texts.add(norm)
        src_item = items[primary_idx - 1]
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
        # V4-Flash reasoning 占用 ~50%,留双份空间
        max_tokens=2200,
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


_SILENCE_INSTRUCTION = """\
任务:今日所有关键人物都没有合格发言(无演讲、无采访、无正式声明)。
请写**一句**短小有古典韵味的中文(12-25 字),用作晨报「关键发言」章节的占位语,
让读者会心一笑或停顿一秒,而不是干巴巴的"今日无人发言"。

【风格基调】
- 化用古典意象、诗意句式,但不直接引用名句
- 节制、含蓄,有哲思而不说教
- 例如(只是范围参考,绝不要照抄):"群贤皆默,市自为声""智者三缄其口,世仍奔流不息"
  "今日大音希声"
- **不要**写"今日大佬未发言"这种平白叙述

【硬约束】
- 只输出**那句话本身**,不带前言、不带解释、不带 markdown、不带引号
- 字数 12-25 字,绝不超过 25 字
- 不要用"今天/今日"等明显时间副词
"""


def generate_silence_note(client: LLMClient) -> str | None:
    """全员未发言时生成的占位语。失败返回 None,模板用兜底文案。"""
    resp = client.chat(
        "请写一句替代'关键发言'章节的占位语",
        task_extra=_SILENCE_INSTRUCTION,
        max_tokens=300,
        temperature=0.85,
    )
    text = (resp.text or "").strip().strip("\"'“”「」 ")
    if not text:
        logger.warning("figure_silence.failed reason=%s", resp.error)
        return None
    if text.startswith("```"):
        text = text.strip("` \n")
    if len(text) > 50:
        text = text[:50].rstrip("。!?,;:") + "。"
    logger.info("figure_silence.ok chars=%d", len(text))
    return text
