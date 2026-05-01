"""
宏观新闻头条 → 主题分段叙述(模块 4 加工,M4 内修复后版本)。

输出 2-3 段,每段一个主题词开头,段末 <sup>[N]</sup>。
返回 dict { summary_html, footnotes } 或 None。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from src.collectors.macro_news import MacroFeedBundle, MacroNewsItem
from src.processors.llm_client import LLMClient

logger = logging.getLogger(__name__)


@dataclass
class Footnote:
    index: int
    url: str
    source: str


@dataclass
class MacroNewsSummary:
    summary_html: str
    footnotes: list[Footnote] = field(default_factory=list)


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
- 一段可引用 1-2 个 [N];多源同主题在一段
- [N] 直接对应下面"输入数据"里这条新闻的"#" 编号(我已预编号)

【风格】

- 平实陈述事实,不评论、不展望
- 数字用阿拉伯数字
- 不写"今日宏观主要看点"等导语
- 不要前言、总结
- 输出**只有** 2-3 个 <p> 标签
- 最少 2 段,最多 3 段
"""


_FOOTNOTE_RE = re.compile(
    r"<sup>\[(\d+)\]</sup>"
    r"|\[(\d+)\](?![a-zA-Z\d])"
    r"|\^(\d+)\^"
)


def _re_idx(match: re.Match[str]) -> int:
    return int(match.group(1) or match.group(2) or match.group(3))


def _format_input(bundles: list[MacroFeedBundle]) -> tuple[str, list[MacroNewsItem]]:
    flat_items: list[MacroNewsItem] = []
    lines: list[str] = []
    for b in bundles:
        if b.error or not b.items:
            continue
        lines.append(f"【{b.source}】")
        for it in b.items[:8]:
            flat_items.append(it)
            n = len(flat_items)
            lines.append(f"  #{n}. {it.title}")
    return "\n".join(lines), flat_items


def _rebuild_footnotes(html: str, flat_items: list[MacroNewsItem]) -> tuple[str, list[Footnote]]:
    """重新编号 [N]:按出现顺序连续 1,2,3...
    Bug 修复:旧版用 enumerate 给 new_idx,中间越界条目被跳过会导致 new_idx
    跳号(如 1,3 缺 2)。改用独立 counter,只在真正写入 footnotes 时递增。"""
    used_indexes: list[int] = []
    for m in _FOOTNOTE_RE.finditer(html):
        idx = _re_idx(m)
        if idx not in used_indexes:
            used_indexes.append(idx)

    footnotes: list[Footnote] = []
    rewrite: dict[int, int] = {}
    skipped: list[int] = []
    new_idx = 0  # 只在真正加入 footnotes 时才递增
    for old_idx in used_indexes:
        if not (1 <= old_idx <= len(flat_items)):
            skipped.append(old_idx)
            continue
        it = flat_items[old_idx - 1]
        if not it.url:
            skipped.append(old_idx)
            continue
        new_idx += 1
        footnotes.append(Footnote(index=new_idx, url=it.url, source=it.source or ""))
        rewrite[old_idx] = new_idx
    if skipped:
        logger.warning(
            "macro_filter.footnote_dropped indexes=%s flat_items=%d "
            "(LLM 越界引用或空 url)",
            skipped, len(flat_items),
        )

    url_by_new_idx = {f.index: f.url for f in footnotes}

    def _sub(match: re.Match[str]) -> str:
        old = _re_idx(match)
        if old not in rewrite:
            return ""
        new_i = rewrite[old]
        url = url_by_new_idx.get(new_i, "")
        return (
            f'<sup><a href="{url}" target="_blank" rel="noopener" '
            f'style="color:#0563C1;text-decoration:none;font-size:11px;'
            f'font-family:Charter,Georgia,serif;margin-left:1px;">'
            f'[{new_i}]</a></sup>'
        )

    new_html = _FOOTNOTE_RE.sub(_sub, html)
    return new_html, footnotes


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
    resp = client.chat(
        payload,
        task_extra=_TASK_INSTRUCTION,
        # 同步 news_summarizer:reasoning 易吃 1500+,留双倍空间
        max_tokens=4000,
        temperature=0.3,
    )
    if not resp.text:
        logger.warning("macro_filter.failed reason=%s", resp.error)
        return None
    body_html, footnotes = _rebuild_footnotes(resp.text.strip(), flat_items)
    logger.info("macro_filter.ok footnotes=%d", len(footnotes))
    return MacroNewsSummary(summary_html=body_html, footnotes=footnotes)
