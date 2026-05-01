"""
关键人物发言筛选与摘要(模块 3a/3b 加工,M5.10 质量门槛 + 跨媒体合并)。

输入:list[FigureBundle](已经过 M3 第一道规则筛选 + 7 天 dedupe)
处理:
  1. 规则层预筛(__pre_rule_filter):候选必须含直接引语标记,否则丢弃
  2. LLM 层判断:每条是否真本人原话 + 同源跨媒体合并 + 提炼关键观点
  3. 文本相似度兜底:对 LLM 漏掉的相似观点,Python 端用 SequenceMatcher
     再去一道(阈值 0.6,合并保留信息更完整的)

输出:list[FigureSummary](人物 → 中文摘要 list)
- items 为空 + fallback_raw 为空 → main.py 端整人物从渲染中剔除
- 全人物均空 → 调 generate_silence_note() 写一句古典韵味占位语
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from src.collectors.figures import FigureBundle, FigureMention
from src.processors.llm_client import LLMClient

logger = logging.getLogger(__name__)


# 直接引语标记词(中英混排,任一命中即视为可能含原话)
_QUOTE_MARKERS_RE = re.compile(
    r"[“”\"]\s*[^“”\"]{6,}\s*[“”\"]"  # 双引号包住的 6+ 字符
    r"|[「」]\s*[^「」]{6,}\s*[「」]"  # 中式引号
    r"|他说|她说|他表示|她表示|他认为|她认为|他指出|她指出"
    r"|表示称|声称|明确表示|公开表示|强调说"
    r"|said|told|stated|told reporters|in an interview|argued|claimed"
    r"|warned|cautioned|noted|admitted|added"
    r"|发声|发表演讲|演讲中|采访中|公开信|致股东信",
    re.IGNORECASE,
)


def _has_quote_marker(item: FigureMention) -> bool:
    """规则层预筛:标题或摘要里含直接引语标记词才进入 LLM 层。"""
    text = f"{item.title or ''} {item.snippet or ''}"
    return bool(_QUOTE_MARKERS_RE.search(text))


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
1. **质量门槛**:判断每条是否真的是**本人公开原话**(直接引语 / 演讲 / 采访 / 正式声明 /
   公开信),且具体观点要**有实质内容**(数字、明确判断、具体事件)。空洞口号("AI 是
   未来""市场需要谨慎")、新闻标题党、媒体转述他人评论一律 no。
2. **跨媒体合并(重要)**:不同媒体(如第一财经、搜狐、Reuters、Bloomberg、CNBC)
   报道同一场演讲/采访/正式声明,即使措辞略有差异也必须**合并为一条**。
3. 对通过 1-2 的条目,提炼 1-2 句中文关键观点(忠实原文,去标题党语气)。

输出严格按以下行格式,不要解释、不要前言:
▦ N: yes | <关键观点中文>             # 单条
▦ N,M[,K]: yes | <关键观点中文>       # M、K 与 N 是同一件事,合并后取 N 为代表
▦ N: no  | <一句话说明为什么淘汰>

【关键要求】
- **绝对不要在观点开头加 "黄仁勋说" / "巴菲特表示" / "但斌认为" 等人名前缀**
  (人物姓名已作为小标题在上方,重复人名很啰嗦)
- 直接输出观点本身,如:"AI 推理需求增长远超预期,数据中心投资仍处早期"
- 忠实原文,不引申、不解读
- 若标题含直接引语(""),优先保留引语原意

【跨媒体合并规则(重中之重!)】
判定"是否同源"用以下信号(任一命中即合并):
- 提到的事件主体一致:同一公司财报、同一只股票、同一场会议名称
- 提到的具体数字接近:股价、估值、百分比基本相同
- 时间窗口相近:都是当周或近 3 天内的报道
- 摘要里描述的"是什么场合"一致:都说"在某次访谈中""在公开信中"

具体例子:
- 输入 1: 标题=但斌:看好 AI 前景,加仓英伟达 / 来源=第一财经
        2: 标题=但斌发声:AI 仍是未来主线 / 来源=搜狐
   → **必合并** 输出 "1,2: yes | <提炼后观点>"
- 输入 1: 黄仁勋 GTC 演讲谈推理需求 / Reuters
        2: NVIDIA CEO at GTC: inference demand surging / Bloomberg
        3: 老黄:AI 工厂时代来临 / CNBC
   → **必合并** 输出 "1,2,3: yes | <提炼>"
- 输入 1: 巴菲特谈苹果 / 2: 巴菲特谈中国市场
   → **保持独立**(不同观点不合并)

【硬约束】
- 合并时主索引(N)取**信息最完整、来源最权威**的那条(优先 Reuters/Bloomberg/FT/
  WSJ > 国内财经媒体 > 门户聚合)
- 输出去重后每条观点必须**独立**,读者不应看到两条说同一件事
- 每个输入索引必须出现在某行中**只一次**

【no 行要求】
- 一句话说明淘汰原因(他人转述 / 市场评论 / 二次解读 / 广告软文 / 空洞口号 / 列表帖)
- no 行不展示给读者,只供我们看 LLM 判断依据
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


def _normalize(text: str) -> str:
    """归一化:去空白 + 去标点 + 小写,用于相似度算法。"""
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"[,。!?:;、—\-()()【】《》\"\"'']", "", text)
    return text.lower()


def _similar(a: str, b: str, threshold: float = 0.6) -> bool:
    """SequenceMatcher 文本相似度;阈值 0.6 适合中文新闻标题/观点合并。"""
    return SequenceMatcher(None, _normalize(a), _normalize(b)).ratio() >= threshold


def _parse_output(text: str, items: list[FigureMention]) -> list[FigureKeyPoint]:
    kept: list[FigureKeyPoint] = []
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
        # 兜底相似度去重:LLM 万一漏判,Python 端再做一道
        # 阈值 0.6 适合中文短句:即便措辞不同但讲同一事件也会被合并
        is_dup = any(_similar(body, k.text) for k in kept)
        if is_dup:
            continue
        src_item = items[primary_idx - 1]
        kept.append(FigureKeyPoint(
            text=body,
            source_url=src_item.url,
            source_name=src_item.source,
        ))
    return kept


def filter_one(bundle: FigureBundle, *, client: LLMClient, max_items: int = 5) -> FigureSummary:
    """加工单个人物。
    流程:
      1. 规则层:候选必须含直接引语标记(双引号包句、说/表示、said/told 等)
      2. 全部不含 → 直接返回空(items=空 / fallback_raw=空 → main.py 整人剔除)
      3. LLM 层:质量门槛 + 跨媒体合并 + 提炼观点
      4. Python 端再做相似度兜底
    """
    if bundle.error or not bundle.items:
        return FigureSummary(person=bundle.person, error=bundle.error)
    feed_items = bundle.items[:max_items]
    # 规则层预筛
    qualified = [it for it in feed_items if _has_quote_marker(it)]
    logger.info(
        "figure_filter.rule_pass person=%s in=%d qualified=%d",
        bundle.person, len(feed_items), len(qualified),
    )
    if not qualified:
        # 没有候选含直接引语 → 跳过 LLM,该人物当天**不渲染**
        return FigureSummary(person=bundle.person)

    payload = _format_input(qualified)
    resp = client.chat(
        payload,
        task_extra=_TASK_INSTRUCTION.replace("人物", bundle.person),
        max_tokens=2200,
        temperature=0.2,
    )
    if not resp.text:
        logger.warning("figure_filter.failed person=%s reason=%s", bundle.person, resp.error)
        # LLM 失败:**也不渲染**(不展示原始候选,因为我们在做质量门槛)
        return FigureSummary(person=bundle.person, error=resp.error)
    kept = _parse_output(resp.text, qualified)
    logger.info(
        "figure_filter.ok person=%s qualified=%d kept=%d",
        bundle.person, len(qualified), len(kept),
    )
    # 注意:不再用 fallback_raw 展示原始候选(质量门槛优先于"展示什么")
    return FigureSummary(person=bundle.person, items=kept)


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
