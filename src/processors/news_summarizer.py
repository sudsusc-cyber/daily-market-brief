"""
持仓公司昨日新闻 → "公司分行"简报(模块 1 加工,M4 内修复后版本)。

输入:list[CompanyNewsBundle]
输出:dict { summary_html: str, footnotes: list[Footnote] } 或 None

每家公司单行一条,格式:
  <div>{中文公司名}    {一句话摘要}<sup>[N]</sup></div>

LLM 给出引用编号,Python 端把序号映射回 url + source 拼脚注列表。
失败时返回 None,模板降级到 M3 ticker × 5 新闻列表。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from src.collectors.company_news import CompanyNewsBundle, NewsItem
from src.processors.llm_client import LLMClient

logger = logging.getLogger(__name__)


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
}


_TASK_INSTRUCTION = """\
任务:对给定的"持仓公司当日新闻列表",生成一份"公司分行"的中文简报。

【输出格式】

每家公司输出一行,格式严格为:
  <strong>公司中文名</strong> —— 一句话摘要<sup>[N]</sup>

- <strong>...</strong> 标签包住公司名,标签必须出现
- 公司中文名后接全角破折号 —— 然后接摘要(用于视觉分隔)
- 公司中文名:4-6 字常用译名(微软、好市多、苹果、英伟达、台积电、穆迪、
  谷歌、伯克希尔、可口可乐、运通、腾讯、泡泡玛特)
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


# 兼容 LLM 输出的多种引用变体:
#   <sup>[N]</sup> / <sup>(N)</sup> / <sup>【N】</sup>  ← 标准上标
#   [N] / (N) / 【N】 / (N) — 后不跟字母数字时认作引用 ← 裸括号
#   ^N^                                                ← markdown
# 中英括号都接;括号内可有空格(\s*);避免 URL / 公式中误匹配。
_FOOTNOTE_RE = re.compile(
    r"<sup>\s*[\[【(\(]\s*(\d+)\s*[\]】)\)]\s*</sup>"
    r"|[\[【(\(]\s*(\d+)\s*[\]】)\)](?![a-zA-Z\d])"
    r"|\^(\d+)\^"
)


def _re_idx(match: re.Match[str]) -> int:
    """三组互斥,取非 None 的那个"""
    return int(match.group(1) or match.group(2) or match.group(3))


_CN_NAMES_SORTED: list[str] = sorted(set(_CN_NAME_HINT.values()), key=len, reverse=True)


def _ensure_strong_wrapping(line: str) -> str:
    """LLM 没输出 <strong> 时,用已知中文公司名反查包装。
    若行首匹配已知公司名,wrap 成 `<strong>名</strong> —— 摘要`;否则原样返回。"""
    if "<strong>" in line:
        return line
    for cn in _CN_NAMES_SORTED:
        if line.startswith(cn):
            rest = line[len(cn):].lstrip(" —-—:、。·")
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


def _build_footnotes(
    html: str, flat_items: list[NewsItem]
) -> tuple[str, list[Footnote]]:
    """从 summary html 中提取 [N] 引用,按出现顺序重新编号 1..M。
    返回 (new_html, footnotes),new_html 中的引用已包成 anchor。"""
    used_indexes: list[int] = []
    for m in _FOOTNOTE_RE.finditer(html):
        idx = _re_idx(m)
        if idx not in used_indexes:
            used_indexes.append(idx)
    # 重新编号:用户读到的 [1] [2] ... 必须按出现顺序连续排,中间不能跳号
    # 修复:旧版 enumerate 在中间项越界跳过时 new_idx 不回退导致跳号(1,3 缺 2)
    footnotes: list[Footnote] = []
    rewrite: dict[int, int] = {}
    skipped: list[int] = []
    new_idx = 0
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
        logger.warning("news_summarizer.footnote_dropped indexes=%s flat_items=%d",
                       skipped, len(flat_items))
    # 把 html 里的旧 [N] 改成新顺序,且包成 oxblood 无下划线 <a>
    url_by_new_idx = {f.index: f.url for f in footnotes}

    def _sub(match: re.Match[str]) -> str:
        old = _re_idx(match)
        if old not in rewrite:
            return ""  # 没有 url 的脚注被剥掉
        new_idx = rewrite[old]
        url = url_by_new_idx.get(new_idx, "")
        return (
            f'<sup><a href="{url}" target="_blank" rel="noopener" '
            f'style="color:#0563C1;text-decoration:none;font-size:11px;'
            f'font-family:Charter,Georgia,serif;margin-left:1px;">'
            f'[{new_idx}]</a></sup>'
        )

    new_html = _FOOTNOTE_RE.sub(_sub, html)
    return new_html, footnotes


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
    resp = client.chat(
        payload,
        task_extra=_TASK_INSTRUCTION,
        # V4-Flash reasoning 易吃 3500+ token;给可见输出留足空间
        max_tokens=6000,
        temperature=0.4,
    )
    if not resp.text:
        logger.warning("news_summarizer.failed reason=%s", resp.error)
        return None

    raw_html = resp.text.strip()
    lines = [line.strip() for line in raw_html.splitlines() if line.strip()]
    cleaned = []
    for line in lines:
        # 若 LLM 自己写了 "1." "1、" 之类前缀,剥掉
        line = re.sub(r"^[0-9]+[.、。\s]+", "", line)
        # Fallback:LLM 没输出 <strong> 时,用已知中文公司名反查包成 <strong>
        line = _ensure_strong_wrapping(line)
        cleaned.append(line)

    # 拆出 (公司名, 摘要);LLM 偶尔不带分隔符,兜底进无公司名条目。
    row_re = re.compile(r"<strong>(.+?)</strong>\s*[——\-:、]+\s*(.+)")
    rows: list[tuple[str, str]] = []
    for line in cleaned:
        m = row_re.match(line)
        if m:
            rows.append((m.group(1).strip(), m.group(2).strip()))
        else:
            stripped = re.sub(r"</?strong>", "", line)
            rows.append(("", stripped))

    # M5.10 视觉:每条新闻渲染为单行的 "公司名 │ 摘要"。
    # - 公司名:与正文同字号同字重同字体,仅用 oxblood 强调色 + 极轻字距区分
    # - 分隔符:U+2502 竖线,左右空格,浅灰(color_hairline #D9D2BE)
    # - 摘要:正文样式 16px / 1.9 / 黑色
    name_style = (
        "color:#7A1F2B; letter-spacing:0.04em;"  # oxblood 同 DAILY BRIEF;轻字距而非宽
    )
    sep_style = (
        "color:#D9D2BE; margin:0 6px;"  # 浅灰竖线,与摘要 letter-spacing 对齐
    )
    row_style = (
        "margin:0 0 10px 0; padding:0;"
        "font-family:'Noto Serif SC','Source Han Serif SC','Songti SC','STSong',Charter,Cambria,Georgia,serif;"
        "font-size:16px; line-height:1.9; color:#1A1A1A; letter-spacing:0.02em;"
    )
    body_html = ""
    for cn, summary in rows:
        if cn:
            body_html += (
                f'<div style="{row_style}">'
                f'<span style="{name_style}">{cn}</span>'
                f'<span style="{sep_style}">│</span>'
                f'{summary}'
                "</div>"
            )
        else:
            # 无公司名:整行作为正文(全部公司无新闻时的兜底文本)
            body_html += f'<div style="{row_style}">{summary}</div>'

    body_html, footnotes = _build_footnotes(body_html, flat_items)
    logger.info("news_summarizer.ok rows=%d footnotes=%d", len(rows), len(footnotes))
    return CompanyNewsSummary(summary_html=body_html, footnotes=footnotes)
