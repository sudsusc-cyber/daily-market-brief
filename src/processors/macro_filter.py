"""
宏观新闻头条 → 主题分段叙述(模块 4 加工,M4 内修复后版本)。

输出 2-3 段,每段一个主题词开头,段末 <sup>[N]</sup>。
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
    render_text_with_footnotes,
    safe_anchor,
    strip_all_tags,
)
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
        for it in b.items[:8]:
            flat_items.append(it)
            n = len(flat_items)
            lines.append(f"  #{n}. {it.title}")
    return "\n".join(lines), flat_items


# Python 端写死的段落样式(不接受外部输入,杜绝 style 注入)
_PARAGRAPH_STYLE = (
    "margin:0 0 14px 0; padding:0;"
    "font-family:'Noto Serif SC','Source Han Serif SC','Songti SC','STSong',"
    "Charter,Cambria,Georgia,serif;"
    "font-size:16px; line-height:1.9; color:#1A1A1A; letter-spacing:0.02em;"
)
_THEME_STYLE = "color:#7A1F2B; letter-spacing:0.04em; font-weight:600;"

# 主题词与正文的分隔模式:句号 / 中文句号 / 冒号
_THEME_SPLIT_RE = re.compile(r"^([^。.::]+)[。.::]\s*(.+)$", re.DOTALL)


def _rebuild_safe_html(
    raw_text: str, flat_items: list[MacroNewsItem]
) -> tuple[str, list[Footnote]]:
    """LLM 输出 → 安全 HTML + 脚注列表。

    - LLM 标签全部丢弃(只信任脚注标记 [N] 与段落分隔)
    - 段落识别:按 `<p>...</p>` 拆,失败则按双换行拆
    - 每段:先 strip 所有标签得纯文本,再尝试拆"主题词。正文",
      最后用 render_text_with_footnotes 把脚注 [N] 转成安全 <a>,
      其余文本一律 html.escape
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

    # 全文扫一遍 [N],按出现顺序确定 rewrite + footnotes(URL 走白名单)
    combined = "\n".join(paragraphs_raw)
    used_indexes: list[int] = []
    for m in FOOTNOTE_RE.finditer(combined):
        idx = footnote_idx(m)
        if idx not in used_indexes:
            used_indexes.append(idx)

    footnotes: list[Footnote] = []
    rewrite: dict[int, int] = {}
    skipped: list[int] = []
    new_idx = 0
    for old_idx in used_indexes:
        if not (1 <= old_idx <= len(flat_items)):
            skipped.append(old_idx)
            continue
        it = flat_items[old_idx - 1]
        if not is_safe_url(it.url):  # 仅 http/https
            skipped.append(old_idx)
            continue
        new_idx += 1
        footnotes.append(Footnote(index=new_idx, url=it.url, source=it.source or ""))
        rewrite[old_idx] = new_idx
    if skipped:
        logger.warning(
            "macro_filter.footnote_dropped indexes=%s flat_items=%d "
            "(越界 / URL scheme 非 http(s) / 缺 url)",
            skipped, len(flat_items),
        )

    url_by_new_idx = {f.index: f.url for f in footnotes}

    def _build_anchor(idx: int) -> str:
        new_i = rewrite.get(idx)
        if new_i is None:
            return ""
        url = url_by_new_idx.get(new_i, "")
        anchor = safe_anchor(url, f"[{new_i}]", style=FOOTNOTE_ANCHOR_STYLE)
        return f"<sup>{anchor}</sup>"

    # 重建 HTML:每段一个 <p>,主题词加粗 oxblood
    parts: list[str] = []
    for para in paragraphs_raw:
        m = _THEME_SPLIT_RE.match(para)
        if m:
            theme_text = m.group(1).strip()
            body_text = m.group(2).strip()
            safe_theme = escape_text(theme_text)
            safe_body = render_text_with_footnotes(
                body_text, FOOTNOTE_RE, footnote_idx, _build_anchor,
            )
            parts.append(
                f'<p style="{_PARAGRAPH_STYLE}">'
                f'<span style="{_THEME_STYLE}">{safe_theme}。</span>'
                f'{safe_body}'
                f'</p>'
            )
        else:
            # 无主题词分隔 → 整段当正文
            safe_body = render_text_with_footnotes(
                para, FOOTNOTE_RE, footnote_idx, _build_anchor,
            )
            parts.append(f'<p style="{_PARAGRAPH_STYLE}">{safe_body}</p>')

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
    body_html, footnotes = _rebuild_safe_html(resp.text.strip(), flat_items)
    logger.info("macro_filter.ok footnotes=%d (sanitized)", len(footnotes))
    return MacroNewsSummary(summary_html=body_html, footnotes=footnotes)
