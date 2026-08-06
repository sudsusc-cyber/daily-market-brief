"""
持仓公司昨日新闻 → "公司分行"简报(模块 1 加工,M4 内修复后版本)。

输入:list[CompanyNewsBundle]
输出:dict { summary_html: str, footnotes: list[Footnote] } 或 None

每家公司单行一条,格式:
  <div>{中文公司名}    {一句话摘要}<sup>[N]</sup></div>

LLM 给出引用编号,Python 端把序号映射回 url + source 拼脚注列表。
失败时返回 None,由 main.py 使用受控占位语并记录质量告警。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from src.collectors.company_news import CompanyNewsBundle, NewsItem
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

_MAX_SUMMARY_ATTEMPTS = 2


@dataclass
class Footnote:
    """LLM 段落中 <sup>[N]</sup> 对应的外链脚注"""
    index: int
    url: str
    source: str


@dataclass
class CompanyNewsSummary:
    """LLM 加工产物"""
    summary_html: str
    footnotes: list[Footnote] = field(default_factory=list)
    is_silence: bool = False


# 中文公司名映射(prompt 里展示给 LLM 让它选用,不是 enforce)
_CN_NAME_HINT: dict[str, str] = {
    "MSFT": "微软",
    "COST": "好市多",
    "AAPL": "苹果",
    "NVDA": "英伟达",
    "TSM": "台积电",
    "MCO": "穆迪",
    "GOOG": "谷歌",
    "BRK.B": "伯克希尔",
    "KO": "可口可乐",
    "AXP": "运通",
    "0700.HK": "腾讯",
    "9992.HK": "泡泡玛特",
    "MA": "万事达",
    "LIN": "林德",
}


_TASK_INSTRUCTION = """\
任务:对给定的"持仓公司当日新闻列表",生成一份"公司分行"的中文简报。

【输出格式】

每家公司输出一行,格式严格为:
  <strong>公司中文名</strong> —— 一句话摘要<sup>[N]</sup>

- <strong>...</strong> 标签包住公司名,标签必须出现
- 公司中文名后接全角破折号 —— 然后接摘要(用于视觉分隔)
- 公司中文名:4-6 字常用译名(微软、好市多、苹果、英伟达、台积电、穆迪、
  谷歌、伯克希尔、可口可乐、运通、腾讯、泡泡玛特、万事达、林德)
- 一句话摘要:40-60 字,陈述客观事实
- <sup>[N]</sup> 是脚注序号,N 必须等于下面"输入数据"里这条新闻的"#" 编号
- 当某家公司没有重要新闻时,**这家公司不出现在输出中**(不要写"无重要新闻")
- 当全部公司都没有重要新闻时,只输出一行:持仓今日无重要动态。

【输出示例】

<strong>苹果</strong> —— App Store 抽成案被驳回,案件移交最高法院。<sup>[1]</sup>
<strong>英伟达</strong> —— Arrive AI 部署 Isaac Sim 与 Blackwell GPU 用于机器人视觉训练。<sup>[2]</sup>
<strong>腾讯</strong> —— 4 月获 154 款游戏版号;开源轻量端侧翻译模型。<sup>[3]</sup>

【内容标准】

值得报道:业绩 / 重大合作 / 监管动作 / 产品发布 / 人事变动 / 资本动作 / 并购
不值得报道:股价波动本身、"分析师上调评级"类二手观点、KOL 评论、八卦花边、
  已被市场充分消化的旧闻、列表/排行类文章

【风格】

- 平实自然,严禁 AI 腔
- 主动语态:"苹果暂停 App Store 抽成变更" 而不是被动语态
- 数字用阿拉伯数字
- 不要前言、总结、过渡句
- 输出**只有**公司分行,每行独立一行(用换行分隔),每行必须以 <strong> 开头
"""


_SILENCE_INSTRUCTION = """\
任务:今日所有持仓公司都没有重要新闻(无业绩、无重大合作、无监管动作、无产品发布)。
请写**一句**短小有古典韵味的中文(12-25 字),用作晨报「昨日动态」章节的占位语,
让读者感受到一份从容的留白,而不是干巴巴的"今日无重要新闻"。

【风格基调】
- 聚焦市场与企业的静默感,用古典意象传达"市井无大事,岁月自流转"的意境
- 节制、含蓄,有生意气息但不市侩
- 例如(只是范围参考,绝不要照抄):"商海无波,舟自徐行""旌旗未动,营垒安然"
  "市声远去,只有时间在走""今日无战事,账簿安静如睡莲"
- **不要**写"今日无重要动态"这种平白叙述

【硬约束】
- 只输出**那句话本身**,不带前言、不带解释、不带 markdown、不带引号
- 字数 12-25 字,绝不超过 25 字
- 不要用"今天/今日"等明显时间副词
"""


def generate_silence_note(client: LLMClient) -> str | None:
    """所有持仓公司均无重要新闻时生成的占位语。失败返回 None,模板用兜底文案。"""
    resp = client.chat(
        "请写一句替代'昨日动态'章节的占位语",
        task_extra=_SILENCE_INSTRUCTION,
        max_tokens=256,
        temperature=0.85,
        thinking=False,
    )
    text = (resp.text or "").strip().strip("\"'“”「」 ")
    if not text:
        logger.warning("news_summarizer.silence_failed reason=%s", resp.error)
        return None
    if text.startswith("```"):
        text = text.strip("` \n")
    if len(text) > 50:
        text = text[:50].rstrip("。!?,;:") + "。"
    logger.info("news_summarizer.silence_ok chars=%d", len(text))
    return text


# 兼容 LLM 输出的多种引用变体:
#   <sup>[N]</sup> / <sup>(N)</sup> / <sup>【N】</sup>  ← 标准上标
#   [N] / (N) / 【N】 / (N) — 后不跟字母数字时认作引用 ← 裸括号
#   ^N^                                                ← markdown
# FOOTNOTE_RE / footnote_idx / FOOTNOTE_ANCHOR_STYLE 共用 html_safe.py
# (此前与 macro_filter 各自重复定义,容易漂移)。


_CN_NAMES_SORTED: list[str] = sorted(set(_CN_NAME_HINT.values()), key=len, reverse=True)


def _ensure_strong_wrapping(line: str) -> str:
    """LLM 没输出 <strong> 时,用已知中文公司名反查包装。
    若行首匹配已知公司名,wrap 成 `<strong>名</strong> —— 摘要`;否则原样返回。"""
    if "<strong>" in line:
        return line
    for cn in _CN_NAMES_SORTED:
        if line.startswith(cn):
            rest = line[len(cn):]
            while rest and rest[0] in " —-—:、。·":
                rest = rest[1:]
            return f"<strong>{cn}</strong> —— {rest}"
    return line


def _format_input(bundles: list[CompanyNewsBundle]) -> tuple[str, list[NewsItem]]:
    """
    把 bundles 转成 LLM 输入,同时维护一个全局编号 → NewsItem 的索引,
    LLM 输出的 [N] 对应这个索引。
    """
    flat_items: list[NewsItem] = []
    lines: list[str] = []
    for b in bundles:
        if b.error or not b.items:
            continue
        cn_name = _CN_NAME_HINT.get(b.holding.ticker, b.holding.name)
        head = f"【{cn_name}({b.holding.ticker})】"
        lines.append(head)
        for it in b.items[:5]:
            flat_items.append(it)
            n = len(flat_items)
            src = f" — {it.source}" if it.source else ""
            lines.append(f"  #{n}. {it.title}{src}")
    return "\n".join(lines), flat_items


def _resolve_footnote_mapping(
    text: str, flat_items: list[NewsItem]
) -> tuple[dict[int, int], list[Footnote]]:
    """扫描 text 中所有脚注标记,按首次出现顺序重新编号 1..M。
    丢弃越界 / URL 不安全 / URL 缺失 的引用(其位置后续被替换为空)。

    返回 (rewrite_map, footnote_list)。rewrite_map 把 LLM 原始编号 → 新编号。
    """
    used_indexes: list[int] = []
    for m in FOOTNOTE_RE.finditer(text):
        idx = footnote_idx(m)
        if idx not in used_indexes:
            used_indexes.append(idx)
    rewrite: dict[int, int] = {}
    footnotes: list[Footnote] = []
    skipped: list[int] = []
    new_idx = 0
    for old_idx in used_indexes:
        if not (1 <= old_idx <= len(flat_items)):
            skipped.append(old_idx)
            continue
        it = flat_items[old_idx - 1]
        if not is_safe_url(it.url):  # 仅 http/https 通过
            skipped.append(old_idx)
            continue
        new_idx += 1
        footnotes.append(Footnote(
            index=new_idx,
            url=it.url,
            source=it.source or "",
        ))
        rewrite[old_idx] = new_idx
    if skipped:
        logger.warning(
            "news_summarizer.footnote_dropped indexes=%s flat_items=%d "
            "(越界 / URL scheme 非 http(s) / 缺 url)",
            skipped, len(flat_items),
        )
    return rewrite, footnotes


def _make_footnote_anchor_builder(
    rewrite: dict[int, int],
    footnotes: list[Footnote],
):
    """构造 builder:LLM 原编号 → 安全 <sup><a>...</a></sup> 字符串。
    URL 已在 _resolve_footnote_mapping 通过 is_safe_url 过滤,这里再走 safe_anchor
    做二次防御(escape href 与 label)。"""
    url_by_new = {f.index: f.url for f in footnotes}

    def _build(idx: int) -> str:
        new_i = rewrite.get(idx)
        if new_i is None:
            return ""  # 越界 / 不安全:吃掉脚注标记
        url = url_by_new.get(new_i, "")
        anchor = safe_anchor(url, f"[{new_i}]", style=FOOTNOTE_ANCHOR_STYLE)
        return f"<sup>{anchor}</sup>"

    return _build


def _render_summary_segment(
    text: str,
    rewrite: dict[int, int],
    footnotes: list[Footnote],
) -> str:
    """对一段不可信文本 segment(LLM 输出),生成安全 HTML:
    标记之间纯文本 escape,标记位置插入安全 <sup><a>。"""
    builder = _make_footnote_anchor_builder(rewrite, footnotes)
    return render_text_with_footnotes(text, FOOTNOTE_RE, footnote_idx, builder)


def _is_no_important_output(text: str) -> bool:
    """识别 prompt 约定的“全部不重要”有效结果，避免误判成解析失败。"""
    cleaned = strip_all_tags(text).strip()
    cleaned = re.sub(r"[\s。.!！?？,，;；:：]+", "", cleaned)
    return cleaned in {"持仓今日无重要动态", "持仓无重要动态"}


def _rebuild_safe_summary(
    raw_text: str,
    flat_items: list[NewsItem],
) -> CompanyNewsSummary | None:
    """把单次 LLM 输出重建为安全 HTML；无有效来源时返回 None。"""
    # LLM 输出含 <strong>...</strong> 与脚注标记,但其余 HTML 一律视为不可信。
    # 处理流程(净化优先):
    #   1. 拆行
    #   2. 用 row_re 抠出 <strong>cn</strong> ... 的"公司名 / 摘要"对(只识别 strong;
    #      其它标签通通视为污染,在第 3 步净化掉)
    #   3. 对 cn 做 strip_all_tags + escape;对 summary 做 strip_all_tags 后送
    #      render_text_with_footnotes(其内部 escape 文本 + 安全替换 [N])
    #   4. Python 端用受控 div/span 拼成最终 HTML
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    cleaned = []
    for line in lines:
        line = re.sub(r"^[0-9]+[.、。\s]+", "", line)
        line = _ensure_strong_wrapping(line)
        cleaned.append(line)

    row_re = re.compile(r"<strong>(.+?)</strong>\s*[——\-:、]+\s*(.+)", re.DOTALL)
    raw_rows: list[tuple[str, str]] = []
    for line in cleaned:
        match = row_re.match(line)
        if match:
            raw_rows.append((match.group(1).strip(), match.group(2).strip()))
        else:
            raw_rows.append(("", strip_all_tags(line)))

    combined_for_scan = "\n".join(f"{cn} {summary}" for cn, summary in raw_rows)
    rewrite, footnotes = _resolve_footnote_mapping(combined_for_scan, flat_items)

    name_style = "color:#7A1F2B; letter-spacing:0.04em;"
    sep_style = "color:#D9D2BE; margin:0 6px;"
    row_style = (
        "margin:0 0 10px 0; padding:0;"
        "font-family:'Noto Serif SC','Songti SC','SimSun',Georgia,serif;"
        "font-size:16px; line-height:1.9; color:#1A1A1A; letter-spacing:0.02em;"
    )

    rendered_rows: list[str] = []
    for cn, summary in raw_rows:
        cited_indexes = {footnote_idx(match) for match in FOOTNOTE_RE.finditer(summary)}
        if not any(index in rewrite for index in cited_indexes):
            logger.warning(
                "news_summarizer.row_dropped_without_valid_source company=%r",
                strip_all_tags(cn)[:40],
            )
            continue
        safe_cn = escape_text(strip_all_tags(cn))
        clean_summary_text = strip_all_tags(summary)
        safe_summary = _render_summary_segment(clean_summary_text, rewrite, footnotes)
        if safe_cn:
            rendered_rows.append(
                f'<div style="{row_style}">'
                f'<span style="{name_style}">{safe_cn}</span>'
                f'<span style="{sep_style}">│</span>'
                f'{safe_summary}'
                "</div>"
            )
        else:
            rendered_rows.append(f'<div style="{row_style}">{safe_summary}</div>')

    if not rendered_rows:
        return None
    return CompanyNewsSummary(
        summary_html="".join(rendered_rows),
        footnotes=footnotes,
    )


def summarize(
    bundles: list[CompanyNewsBundle],
    *,
    client: LLMClient,
) -> CompanyNewsSummary | None:
    if not bundles:
        return None
    payload, flat_items = _format_input(bundles)
    if not payload.strip():
        return None
    last_error: str | None = None
    for attempt in range(1, _MAX_SUMMARY_ATTEMPTS + 1):
        task_instruction = _TASK_INSTRUCTION
        if attempt > 1:
            task_instruction += """

【重试修正】上一次输出为空、格式不完整或没有有效来源脚注。这次必须直接按约定
逐行输出；若全部不重要，只输出“持仓今日无重要动态。”。
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
                "news_summarizer.attempt_failed attempt=%d/%d reason=%s",
                attempt, _MAX_SUMMARY_ATTEMPTS, last_error,
            )
            continue

        raw_text = resp.text.strip()
        if _is_no_important_output(raw_text):
            logger.info("news_summarizer.silence attempt=%d", attempt)
            return CompanyNewsSummary(summary_html="", is_silence=True)

        summary = _rebuild_safe_summary(raw_text, flat_items)
        if summary is not None:
            logger.info(
                "news_summarizer.ok footnotes=%d attempt=%d (sanitized)",
                len(summary.footnotes), attempt,
            )
            return summary

        last_error = "AllRowsDroppedWithoutValidSources"
        logger.warning(
            "news_summarizer.invalid_output attempt=%d/%d reason=%s",
            attempt, _MAX_SUMMARY_ATTEMPTS, last_error,
        )

    logger.warning(
        "news_summarizer.failed attempts=%d reason=%s",
        _MAX_SUMMARY_ATTEMPTS, last_error or "unknown",
    )
    return None
