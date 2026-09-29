"""
宏观新闻头条 → 主题分段叙述(模块 4 加工,M4 内修复后版本)。

同主题的独立事实同段展示,每条事实紧跟自己的 <sup>[N]</sup>。
返回 dict { summary_html, footnotes } 或 None。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from src.collectors.macro_news import MacroFeedBundle, MacroNewsItem
from src.processors.html_safe import (
    FOOTNOTE_ANCHOR_STYLE,
    FOOTNOTE_RE,
    escape_text,
    footnote_idx,
    is_safe_url,
    safe_anchor,
    strip_all_tags,
)
from src.processors.llm_client import LLMClient
from src.processors.news_selection import macro_candidate, neutral_macro_topic
from src.processors.source_grounding import INSTRUCTION, grounded_text, source_prompt
from src.utils.email_typography import EMAIL_EDITORIAL_SERIF
from src.utils.news_facts import canonical_fact

logger = logging.getLogger(__name__)

_MAX_SUMMARY_ATTEMPTS = 2


@dataclass
class Footnote:
    index: int
    url: str
    source: str


@dataclass
class MacroNewsSummary:
    summary_html: str
    footnotes: list[Footnote] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)


_TASK_INSTRUCTION = """\
任务:从下列过去 24 小时各财经媒体头条中,筛选出**真正影响全球市场或重大经济**的
头版级新闻,然后按主题归类成 2-3 段中文叙述。

【筛选标准】

只保留:
- 央行政策(Fed / ECB / BOJ / PBOC 利率决议或会议纪要)
- 重大地缘政治(中美 / 台海 / 欧洲 / 中东冲突)
- 影响万亿级资产的监管变动
- 宏观数据(非农 / CPI / GDP / PMI)
- 系统性风险事件

排除:个股新闻、行业评论、二手观点、人物花边、政治选举日常

【输出结构】

把筛选后的 3-5 条新闻,按主题归类成 **2-3 段**,每段聚焦一个主题。

每段格式严格为:
  <p><strong>主题词。</strong>主题陈述 80-120 字,段末用 <sup>[N]</sup> 标注脚注。</p>

- 主题词:3-4 字(能源市场 / 中国经济 / 地缘政治 / 美联储 / 通胀数据 等)
- 主题词后用句号分隔正文(不是冒号)
- 一段可引用 1-2 个 <sup>[N]</sup>;多源同主题在一段
- <sup>[N]</sup> 中的 N 是阿拉伯数字(如 [1]、[2]),**不要**写成 [#1] 或 [#N];
  N 必须等于下面"输入数据"里这条新闻的"#" 编号(我已预编号)

【风格】

- 平实陈述事实,不评论、不展望
- 数字用阿拉伯数字
- 不写"今日宏观主要看点"等导语
- 不要前言、总结
- 输出**只有** 2-3 个 <p> 标签
- 最少 2 段,最多 3 段
"""


_SILENCE_INSTRUCTION = """\
任务:今日各财经媒体均无真正的头版级宏观新闻(无央行决议、无地缘冲突升级、
无系统性风险事件、无重大宏观数据)。请写**一句**短小有古典韵味的中文(12-25 字),
用作晨报「宏观视野」章节的占位语,传达出天地间暂时的平静与从容。

【风格基调】
- 聚焦时间流逝与世界的静默,用天文、气象、山水等自然意象
- 开阔、从容,有大历史感但不装腔
- 例如(只是范围参考,绝不要照抄):"四海无波,日升月落而已""风未起,江湖自平"
  "穹顶之下,万物有序,今日无惊雷""星图如昨,寰宇安然"
- **不要**写"今日无宏观新闻"这种平白叙述

【硬约束】
- 只输出**那句话本身**,不带前言、不带解释、不带 markdown、不带引号
- 字数 12-25 字,绝不超过 25 字
- 不要用"今天/今日"等明显时间副词
"""


def generate_silence_note(client: LLMClient) -> str | None:
    """无宏观头条时生成的占位语。失败返回 None,模板用兜底文案。"""
    resp = client.chat(
        "请写一句替代'宏观视野'章节的占位语",
        task_extra=_SILENCE_INSTRUCTION,
        max_tokens=256,
        temperature=0.85,
        thinking=False,
    )
    text = (resp.text or "").strip().strip("\"'“”「」 ")
    if not text:
        logger.warning("macro_filter.silence_failed reason=%s", resp.error)
        return None
    if text.startswith("```"):
        text = text.strip("` \n")
    if len(text) > 50:
        text = text[:50].rstrip("。!?,;:") + "。"
    logger.info("macro_filter.silence_ok chars=%d", len(text))
    return text


# 兼容 LLM 输出的多种引用变体:
#   <sup>[N]</sup> / <sup>(N)</sup> / <sup>【N】</sup>  ← 标准上标
#   [N] / (N) / 【N】 / (N) — 后不跟字母数字时认作引用 ← 裸括号
#   ^N^                                                ← markdown
# FOOTNOTE_RE / footnote_idx / FOOTNOTE_ANCHOR_STYLE 共用 html_safe.py
# (此前与 news_summarizer 各自重复定义,容易漂移)。


def _format_input(bundles: list[MacroFeedBundle]) -> tuple[str, list[MacroNewsItem]]:
    flat_items: list[MacroNewsItem] = []
    lines: list[str] = []
    for b in bundles:
        if b.error or not b.items:
            continue
        lines.append(f"【{b.source}】")
        for it in [item for item in b.items if macro_candidate(item)][:8]:
            flat_items.append(it)
            n = len(flat_items)
            lines.append(f"  #{n}. 原始标题={it.title} {source_prompt(it)}\n原始摘要={it.summary}")
    return "\n".join(lines), flat_items


# Python 端写死的段落样式(不接受外部输入,杜绝 style 注入)
_PARAGRAPH_STYLE = (
    "margin:0 0 14px 0; padding:0;"
    f"font-family:{EMAIL_EDITORIAL_SERIF};"
    "font-size:16px; line-height:1.9; color:#1A1A1A; letter-spacing:0.02em;"
)
_THEME_STYLE = "color:#7A1F2B; letter-spacing:0.04em; font-weight:600;"

# 主题词与正文的分隔模式:句号 / 中文句号 / 冒号
_THEME_SPLIT_RE = re.compile(r"^([^。.::]+)[。.::]\s*(.+)$", re.DOTALL)


def _rebuild_safe_html(
    raw_text: str, flat_items: list[MacroNewsItem], evidence: list[dict] | None = None
) -> tuple[str, list[Footnote]]:
    """LLM 输出 → 安全 HTML + 脚注列表。

    - LLM 标签全部丢弃(只信任脚注标记 [N] 与段落分隔)
    - 段落识别:按 `<p>...</p>` 拆,失败则按双换行拆
    - 逐来源核验后按具体主题分段,仅相同事实合并来源
    - 只给实际刊出的事实分配脚注,主题由来源确定,文本转义后构造 HTML
    - URL scheme 白名单:非 http(s) URL 的脚注被丢弃
    """
    # 段落分割:LLM 通常输出 <p>...</p>,先按 </p> 拆,再各自 strip 标签
    paragraphs_raw: list[str] = []
    if "<p" in raw_text:
        for chunk in re.split(r"</\s*p\s*>", raw_text, flags=re.IGNORECASE):
            text = strip_all_tags(chunk).strip()
            if text:
                paragraphs_raw.append(text)
    if not paragraphs_raw:
        # 无 <p> 包裹:按空行分段
        for chunk in re.split(r"\n\s*\n+", raw_text):
            text = strip_all_tags(chunk).strip()
            if text:
                paragraphs_raw.append(text)
    if not paragraphs_raw:
        paragraphs_raw = [strip_all_tags(raw_text).strip()]

    # A model grouping/citation is not proof that two articles describe one
    # event. Only literally equivalent facts share source links. Related facts
    # may share a theme paragraph, but each retains its own evidence and citation.
    groups = {}
    seen = set()
    for para in paragraphs_raw:
        claim = FOOTNOTE_RE.sub("", para).strip()
        split = _THEME_SPLIT_RE.match(claim)
        claim = split.group(2).strip() if split else claim
        for match in FOOTNOTE_RE.finditer(para):
            index = footnote_idx(match)
            if index in seen or not 1 <= index <= len(flat_items):
                continue
            seen.add(index)
            item = flat_items[index - 1]
            if not is_safe_url(item.url):
                continue
            supported, mapping = grounded_text(claim, [item])
            if not supported or not mapping:
                continue
            key = canonical_fact(supported)
            group = groups.setdefault(key, {"text": supported, "items": [], "evidence": []})
            group["items"].append(item)
            group["evidence"].extend(mapping)

    themes = {}
    for group in groups.values():
        topic = neutral_macro_topic(group["items"], supported_text=group["text"])
        # Unknown topics are not a relationship: do not lump unrelated facts
        # together merely because both received the fallback label.
        key = (topic, canonical_fact(group["text"]) if topic == "宏观动态" else "")
        themes.setdefault(key, []).append(group)

    parts, footnotes = [], []
    for (topic, _), related in themes.items():
        facts = []
        for group in related:
            citations = []
            for item in group["items"]:
                index = len(footnotes) + 1
                footnotes.append(Footnote(index=index, url=item.url, source=item.source or ""))
                citations.append("<sup>" + safe_anchor(item.url, f"[{index}]", style=FOOTNOTE_ANCHOR_STYLE) + "</sup>")
            if evidence is not None:
                evidence.extend(group["evidence"])
            text = group["text"].rstrip()
            if text[-1:] not in '。！？!?':
                text = text.rstrip('.') + '。'
            facts.append(f'<span data-macro-fact="true">{escape_text(text)}{"".join(citations)}</span>')
        parts.append(f'<p style="{_PARAGRAPH_STYLE}">'
                     f'<span style="{_THEME_STYLE}">{escape_text(topic)}。</span>'
                     f'{" ".join(facts)}</p>')
    return "".join(parts), footnotes


def summarize(
    bundles: list[MacroFeedBundle],
    *,
    client: LLMClient,
) -> MacroNewsSummary | None:
    if not bundles:
        return None
    payload, flat_items = _format_input(bundles)
    if not payload.strip():
        return None
    last_error: str | None = None
    for attempt in range(1, _MAX_SUMMARY_ATTEMPTS + 1):
        task_instruction = _TASK_INSTRUCTION + INSTRUCTION
        if attempt > 1:
            task_instruction += """

【重试修正】上一次输出为空或没有带有效来源脚注。这次必须直接输出
2-3 个 <p> 段落，且每段至少包含一个来自输入编号的 <sup>[N]</sup>。
"""
        resp = client.chat(
            payload,
            task_extra=task_instruction,
            max_tokens=2000,
            temperature=0.2,
            thinking=False,
        )
        if not resp.text:
            last_error = resp.error or "EmptyOutput"
            logger.warning(
                "macro_filter.attempt_failed attempt=%d/%d reason=%s",
                attempt, _MAX_SUMMARY_ATTEMPTS, last_error,
            )
            continue

        evidence = []
        body_html, footnotes = _rebuild_safe_html(resp.text.strip(), flat_items, evidence)
        if body_html:
            logger.info(
                "macro_filter.ok footnotes=%d attempt=%d (sanitized)",
                len(footnotes), attempt,
            )
            return MacroNewsSummary(summary_html=body_html, footnotes=footnotes, evidence=evidence)

        last_error = "AllParagraphsDroppedWithoutValidSources"
        logger.warning(
            "macro_filter.invalid_output attempt=%d/%d reason=%s",
            attempt, _MAX_SUMMARY_ATTEMPTS, last_error,
        )

    logger.warning(
        "macro_filter.failed attempts=%d reason=%s",
        _MAX_SUMMARY_ATTEMPTS, last_error or "unknown",
    )
    return None
