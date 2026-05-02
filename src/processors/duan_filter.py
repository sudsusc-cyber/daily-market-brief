"""
段永平雪球短文相关性筛选 + 原帖上下文 LLM 压缩。

两个独立 LLM 步骤,严格分工:

1. judge_relevance(yes/no 判定):
   - 与投资 / 公司 / 行业 / 估值 / 商业模式相关 → 保留
   - 纯日常闲聊、生活琐事、运动节庆、个人感受 → 丢弃
   - **绝不改写段永平正文**

2. summarize_parents(原帖压缩):
   - 仅当帖子是回复 / 转评(parent_text 非空)时才调
   - 把原帖压成 ≤ 30 中文字的要点,避免占用「关键发言」过多版面
   - **段永平本人正文不动**,只替换 parent_text 字段

硬约束:段永平的 .text 字段一字不改;只有原帖(parent_text)被压缩。

失败行为(fail-safe):
  - judge_relevance 失败 → 全部丢弃,章节走占位逻辑
  - summarize_parents 失败 → 回退为截取前 50 字 + "…"(原帖至少有上下文,
    不至于断章取义)
"""

from __future__ import annotations

import dataclasses
import logging
import re

from src.collectors.xueqiu_duan import DuanQuote
from src.processors.llm_client import LLMClient

logger = logging.getLogger(__name__)

PARENT_SUMMARY_MAX_CHARS = 30      # 总结目标字数(LLM 出口控制)
PARENT_FALLBACK_TRUNCATE = 50      # LLM 失败时的硬截断长度


_TASK_INSTRUCTION = """\
任务:逐条判断段永平的雪球短文是否值得收入晨报「关键发言」段。对每一条只输出
yes 或 no,**绝不改写或总结正文**。

段永平不是普通分析师,他的真正价值除了具体的投资 / 公司判断之外,还包括:
  - 长期一贯的"本分""平常心""慢即是快"等思维方法论
  - 决策框架(stop doing list、利益驱动 vs 价值驱动、不懂不做)
  - 商业 / 哲学引申(借生活事例讲企业经营、护城河、复利)
  - 对企业家、企业治理的人生哲理评论
**这些哲理性 / 方法论性发言同样要 yes**——读者要的就是这种段永平特色。

判定标准:
- yes(收入):
    1. 具体的股票 / 公司 / 行业 / 商业模式 / 估值 / 资本配置判断
    2. 投资思维方法论 / 决策框架 / 长期主义 / 概率思维 / "本分"等核心理念
    3. 借生活、运动、家事引申到投资 / 商业 / 决策的哲理思考
    4. 对企业家、管理层、商业事件的评价(含负面案例剖析)
    5. 哲学 / 价值观 / 人生态度的发言,只要能映射到投资或经营心法
- no(剔除):
    1. 纯生活流水账(今天去哪吃饭、谁来串门)无任何引申
    2. 纯运动 / 天气 / 节庆 / 家事的简短陈述
    3. 纯打趣调侃 / 段子 / 表情包文字 / 无信息含量的祝福语
    4. 纯链接转发或纯红包贺词

边界示例(很重要,仔细看):
- "今天遛狗想到护城河,好生意就是这种结构"             → yes(生活引申投资)
- "今天遛狗,天气真好"                                  → no(纯生活)
- "本分就是做对的事,做难而正确的事"                   → yes(核心理念)
- "慢即是快,长期看复利的力量大于短期波动"             → yes(思维方法)
- "做投资最重要的就是知道自己不懂什么"                 → yes(决策框架)
- "周末和朋友打了场高尔夫,赢了两个洞"                  → no(纯运动)
- "苹果回购就是在替股东省钱"                            → yes(企业资本配置)
- "我家院子苹果熟了"                                    → no(纯家事)
- "看了某 CEO 的传记,商业本质就是把简单事重复做对"     → yes(企业经营+哲理)
- "祝大家新年快乐,身体健康"                            → no(纯祝福)
- "stop doing list 比 to-do list 更重要"                → yes(段永平经典决策框架)

输出严格按以下行格式,逐条对应输入索引,不要解释、不要前言:
▦ N: yes
▦ N: no

【硬约束】
- 每个输入索引必须对应一行输出(yes 或 no),不漏判
- 不要输出原文片段,不要写理由,只写 yes 或 no
- 拿不准时:**只要含可映射到投资 / 商业 / 决策心法的成分,就 yes**;
  纯生活无引申才 no。段永平的"哲理性发言"是他的特色,宁可多收一两条也不要漏掉。
"""


_LINE_RE = re.compile(r"^▦\s*(\d+)\s*:\s*(yes|no)\s*$", re.IGNORECASE)


def _format_input(quotes: list[DuanQuote]) -> str:
    """把 quotes 编号传给 LLM。回复型帖子带上"原帖上下文",否则 LLM 看不出
    "想多了" 这种短句到底是不是投资相关。"""
    lines: list[str] = []
    for i, q in enumerate(quotes, start=1):
        if q.parent_text:
            author = f"@{q.parent_author}" if q.parent_author else "@?"
            lines.append(
                f"▦ {i}: 【原帖 {author}】{q.parent_text}\n     【段永平回复】{q.text}"
            )
        else:
            lines.append(f"▦ {i}: {q.text}")
    return "\n".join(lines)


def _parse_verdicts(text: str, n: int) -> dict[int, bool]:
    """解析 LLM 输出 → {index: is_relevant}。漏判的索引视为 no(保守)。"""
    out: dict[int, bool] = {}
    for line in text.splitlines():
        m = _LINE_RE.match(line.strip())
        if not m:
            continue
        idx = int(m.group(1))
        if 1 <= idx <= n:
            out[idx] = m.group(2).lower() == "yes"
    return out


_PARENT_SUMMARY_INSTRUCTION = """\
任务:把若干条"原帖正文"分别压成极短中文摘要,作为段永平回复的上下文。
读者只需看一眼即可理解段永平在回应什么,**不需要原帖的细节**。

【硬约束】
- 每条目标 ≤ 30 个中文字符;最长不超过 35 字
- 只摘核心观点 / 主要主张,不抄原句、不带情绪词、不带"作者认为""他说"
- 输出严格按以下行格式,逐条对应输入索引,不要解释、不要前言:
  ▦ N: <摘要文本>
- 严禁加引号 / markdown / 表情;严禁出现"原帖"、"作者"、"该用户"等元词
- 摘要中不得出现段永平本人的名字
- 拿不准时取主语 + 谓语骨架,例:"茅台估值已是泡沫,应清仓"

例子:
- 输入: ▦ 1: 我觉得茅台 PE=40 已经是历史顶,长期持有逻辑被破坏了,要清仓出
- 输出: ▦ 1: 茅台 PE=40 已是泡沫,应清仓

- 输入: ▦ 1: 苹果今年营收预期下修,中国市场份额连续四个季度下滑,管理层应对乏力
- 输出: ▦ 1: 苹果中国份额持续下滑,管理层应对乏力
"""


_SUMMARY_LINE_RE = re.compile(r"^▦\s*(\d+)\s*:\s*(.+?)\s*$")


def _format_parents_for_summary(quotes: list[DuanQuote]) -> tuple[str, list[int]]:
    """构造 LLM 输入。返回 (payload, source_indices),
    source_indices[i] 给出第 i+1 个 LLM 索引对应的原 quotes 下标。"""
    lines: list[str] = []
    indices: list[int] = []
    for i, q in enumerate(quotes):
        if not q.parent_text:
            continue
        lines.append(f"▦ {len(indices) + 1}: {q.parent_text}")
        indices.append(i)
    return "\n".join(lines), indices


def _parse_summary_output(text: str, n: int) -> dict[int, str]:
    """解析 LLM 输出 → {1-indexed: summary_text}。漏判索引留空。"""
    out: dict[int, str] = {}
    for line in text.splitlines():
        m = _SUMMARY_LINE_RE.match(line.strip())
        if not m:
            continue
        idx = int(m.group(1))
        if 1 <= idx <= n:
            body = m.group(2).strip().strip("\"'“”「」")
            if body:
                out[idx] = body
    return out


def _fallback_truncate(parent_text: str) -> str:
    """LLM 失败时的兜底:硬截断到 PARENT_FALLBACK_TRUNCATE 中文字符。"""
    if len(parent_text) <= PARENT_FALLBACK_TRUNCATE:
        return parent_text
    return parent_text[:PARENT_FALLBACK_TRUNCATE].rstrip() + "…"


def summarize_parents(
    quotes: list[DuanQuote],
    *,
    client: LLMClient,
) -> list[DuanQuote]:
    """对每条带 parent_text 的 quote,把 parent_text 替换为 LLM 压缩后的短摘要。

    流程:
      1. 收集所有带 parent 的 quote,批量送 LLM(单次调用)
      2. LLM 返回逐条短摘要 → 替换 parent_text
      3. 单条摘要超长 → 硬截断到 PARENT_SUMMARY_MAX_CHARS + 5 兜底
      4. LLM 失败 / 漏判某条 → 该 parent 走 _fallback_truncate(50 字 + …)
      5. 段永平本人 .text 字段任何情况下都不改
    """
    if not quotes:
        return []
    # 段永平 .text 不动,这里只可能改 parent_text/parent_author。统一用 dataclasses.replace
    # 构造新对象,避免 mutate 调用方持有的引用。
    payload, indices = _format_parents_for_summary(quotes)
    if not indices:
        # 没有任何 parent 需要压缩,直接返回原列表
        return list(quotes)

    summaries: dict[int, str] = {}
    try:
        resp = client.chat(
            payload,
            task_extra=_PARENT_SUMMARY_INSTRUCTION,
            max_tokens=400,
            temperature=0.2,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("duan_filter.parent_summary.exception exc=%r 走截断兜底", exc)
        resp = None

    if resp is not None and resp.text:
        summaries = _parse_summary_output(resp.text, len(indices))
    else:
        if resp is not None:
            logger.warning(
                "duan_filter.parent_summary.failed reason=%s 走截断兜底",
                resp.error,
            )

    out: list[DuanQuote] = []
    for i, q in enumerate(quotes):
        if not q.parent_text:
            out.append(q)
            continue
        # 找该 quote 在 LLM 索引体系中的位置
        try:
            llm_idx = indices.index(i) + 1
        except ValueError:
            out.append(q)
            continue
        new_parent = summaries.get(llm_idx)
        if new_parent:
            # 多硬截一层防 LLM 不守字数
            if len(new_parent) > PARENT_SUMMARY_MAX_CHARS + 5:
                new_parent = new_parent[:PARENT_SUMMARY_MAX_CHARS].rstrip() + "…"
        else:
            new_parent = _fallback_truncate(q.parent_text)
        out.append(dataclasses.replace(q, parent_text=new_parent))
    logger.info(
        "duan_filter.parent_summary parents=%d llm_summarized=%d fallback=%d",
        len(indices), len(summaries),
        len(indices) - len(summaries),
    )
    return out


def judge_relevance(
    quotes: list[DuanQuote],
    *,
    client: LLMClient,
) -> list[DuanQuote]:
    """逐条判定相关性,只保留 yes 的;原文一字不改。

    LLM 调用失败 → 返回 [](fail-safe)。
    """
    if not quotes:
        return []

    payload = _format_input(quotes)
    resp = client.chat(
        payload,
        task_extra=_TASK_INSTRUCTION,
        max_tokens=600,
        temperature=0.1,
    )
    if not resp.text:
        logger.warning(
            "duan_filter.failed reason=%s 全部丢弃,走占位逻辑(fail-safe)",
            resp.error,
        )
        return []

    verdicts = _parse_verdicts(resp.text, len(quotes))
    kept: list[DuanQuote] = []
    for i, q in enumerate(quotes, start=1):
        # 默认 no:LLM 漏判该索引时偏保守
        if verdicts.get(i, False):
            kept.append(q)
    logger.info(
        "duan_filter.judged in=%d kept=%d (verdicts=%d)",
        len(quotes), len(kept), len(verdicts),
    )
    return kept
