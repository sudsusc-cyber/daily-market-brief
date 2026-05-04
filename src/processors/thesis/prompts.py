"""
LLM evidence 抽取 prompt 模板。

只消费已通过日报筛选的内容，不接触原始新闻。
"""

from __future__ import annotations

import re
from typing import Any

from src.processors.html_safe import strip_all_tags

MAX_ACTIVE_THEMES_IN_PROMPT = 120
MAX_EVIDENCE_ITEMS = 8

SYSTEM_EXTRA = """你今天的额外任务是：从以下已筛选的市场日报内容中，抽取与持仓公司长期投资判断直接相关的 evidence。

只抽取与以下维度相关的内容：
- Owner earnings（企业所有者盈利）的持续性与变化
- 竞争格局与护城河变化
- 资本配置决策（回购、分红、并购、capex）
- 估值假设的前提条件变化

不抽取：
- 短期股价波动
- 技术指标信号
- 市场情绪变化
- 纯宏观数据（除非直接改变某持仓公司的长期假设）
- 已经在日报其他模块充分覆盖的一般新闻

抽取规则：
- strength 1-5：5=直接证伪/强化核心假设，4=提供重要新信息，3=相关但边际
- strength < 3 的不要返回
- direction 四选一：support（强化已有判断）、risk（削弱已有判断）、neutral（事实性信息）、new_variable（全新变量，尚未映射到具体判断）
- horizon 三选一：quarterly（1-2 季度内可见影响）、multi_year（2-5 年影响）、structural（5 年以上结构变化）
- theme 用简短英文短语（lowercase，hyphen 分隔），如 "ai-inference-cost"、"tsm-advanced-node-demand"
- 同一事实不要重复抽取
- 没有 url 就给 null，不要编造
- 最多返回 8 条 evidence；若超过 8 条，只保留 strength 最高、与持仓链影响最直接的 8 条

输出必须是 JSON array：
[
  {
    "source_section": "company_news",
    "source_name": "Reuters",
    "url": "https://...",
    "related_tickers": ["NVDA", "MSFT"],
    "theme": "ai-capex-cycle",
    "direction": "support",
    "strength": 4,
    "horizon": "multi_year",
    "text": "原文中与长期判断相关的关键事实，1-3 句。",
    "why_it_matters": "为什么这个事实影响对 owner earnings / 竞争格局 / 资本配置 / 估值假设的判断，1-3 句。"
  }
]

如果当天没有值得抽取的内容（所有内容的 strength 都 < 3），返回空数组 []。"""


def build_user_prompt(
    *,
    company_news: Any | None = None,
    macro_news: Any | None = None,
    figure_summaries: list[Any] | None = None,
    berkshire_events: Any | None = None,
    frontier_labs_events: list[Any] | None = None,
    active_themes: list[str] | None = None,
) -> str:
    """把当天已筛选内容按 source_section 组装成 prompt。"""
    sections: list[str] = []

    # ── 当前活跃 theme 清单（防漂移）──
    if active_themes:
        lines = ["## 当前活跃的长期判断主题 (Active Themes)", ""]
        lines.append("以下 theme 已存在于系统中。新 evidence 若语义匹配已有 theme，"
                     "**必须复用**已有 theme key；确实新维度才允许新造。")
        lines.append("")
        capped_themes = _cap_active_themes(active_themes)
        for t in capped_themes:
            lines.append(f"- `{t}`")
        if len(active_themes) > len(capped_themes):
            lines.append(
                f"- 注：active themes 只注入前 {MAX_ACTIVE_THEMES_IN_PROMPT} 个，"
                "其余较旧 theme 不展开。"
            )
        lines.append("")
        sections.append("\n".join(lines))

    # ── 持仓公司新闻 ──
    if company_news:
        lines = ["## 持仓公司新闻摘要 (source_section: company_news)"]
        if _looks_like_summary(company_news):
            lines.extend(_format_summary_block(company_news))
        else:
            for bundle in company_news:
                ticker = getattr(bundle, "holding", None)
                ticker_str = ticker.ticker if ticker else "?"
                lines.append(f"\n### {ticker_str}")
                items = getattr(bundle, "items", []) or []
                for item in items[:5]:
                    title = getattr(item, "title", "")
                    url = getattr(item, "url", "")
                    summary = getattr(item, "summary", "") or ""
                    source = getattr(item, "source", "")
                    lines.append(f"- [{title}]({url}) — {source}")
                    if summary:
                        lines.append(f"  摘要: {summary}")
        sections.append("\n".join(lines))

    # ── 宏观视野 ──
    if macro_news:
        lines = ["## 宏观视野保留条目 (source_section: macro)"]
        if _looks_like_summary(macro_news):
            lines.extend(_format_summary_block(macro_news))
        else:
            for bundle in macro_news:
                source = getattr(bundle, "source", "?")
                lines.append(f"\n### {source}")
                items = getattr(bundle, "items", []) or []
                for item in items[:5]:
                    title = getattr(item, "title", "")
                    url = getattr(item, "url", "")
                    lines.append(f"- [{title}]({url})")
        sections.append("\n".join(lines))

    # ── 关键人物发言 ──
    if figure_summaries:
        lines = ["## 关键人物发言摘要 (source_section: voices)"]
        for fig in figure_summaries:
            person = getattr(fig, "person", "?")
            lines.append(f"\n### {person}")
            items = getattr(fig, "items", []) or []
            for item in items:
                text = getattr(item, "text", "")
                url = getattr(item, "source_url", "")
                if url:
                    lines.append(f"- {text} ([来源]({url}))")
                else:
                    lines.append(f"- {text}")
        sections.append("\n".join(lines))

    # ── Frontier Labs ──
    if frontier_labs_events:
        lines = ["## Frontier Labs 模块输出 (source_section: frontier_labs)"]
        for item in frontier_labs_events:
            if isinstance(item, dict):
                lines.append(
                    f"- {item.get('lab', '')}: {item.get('text','')} "
                    f"(source: {item.get('source_name','')}, "
                    f"url: {item.get('url') or item.get('source_url','')}, "
                    f"score: {item.get('score','')}, "
                    f"tickers: {','.join(item.get('related_tickers', []) or [])})"
                )
            else:
                tickers = ",".join(getattr(item, "related_tickers", []) or [])
                lines.append(
                    f"- {getattr(item, 'lab', '')}: {getattr(item, 'text', '')} "
                    f"(source: {getattr(item, 'source_name', '')}, "
                    f"url: {getattr(item, 'source_url', '')}, "
                    f"score: {getattr(item, 'score', '')}, "
                    f"tickers: {tickers})"
                )
        sections.append("\n".join(lines))

    # ── Berkshire 事件 ──
    if berkshire_events:
        lines = ["## Berkshire 13F / 年报 / 股东信 (source_section: berkshire)"]
        if hasattr(berkshire_events, "latest"):
            latest = berkshire_events.latest
            lines.append(f"- accession: {getattr(latest, 'accession_no', '?')}")
            lines.append(f"- filed_at: {getattr(latest, 'filed_at', '?')}")
        lines.append(f"- is_new: {getattr(berkshire_events, 'is_new', False)}")
        sections.append("\n".join(lines))

    if not sections:
        return "（今日无已筛选内容。）"

    return "\n\n".join(sections)


def _looks_like_summary(obj: Any) -> bool:
    return hasattr(obj, "summary_html")


def _cap_active_themes(active_themes: list[str]) -> list[str]:
    capped: list[str] = []
    seen: set[str] = set()
    for theme in active_themes:
        t = str(theme).strip()
        if not t or t in seen:
            continue
        capped.append(t)
        seen.add(t)
        if len(capped) >= MAX_ACTIVE_THEMES_IN_PROMPT:
            break
    return capped


def _html_to_text(html: str | None) -> str:
    if not html:
        return ""
    text = re.sub(r"</(?:p|div|li|tr)\s*>", "\n", str(html), flags=re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = strip_all_tags(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def _format_summary_block(summary: Any) -> list[str]:
    lines: list[str] = []
    text = _html_to_text(getattr(summary, "summary_html", ""))
    if text:
        lines.append("")
        lines.append(text)

    footnotes = getattr(summary, "footnotes", []) or []
    if footnotes:
        lines.append("")
        lines.append("来源:")
        for f in footnotes:
            idx = getattr(f, "index", "?")
            source = getattr(f, "source", "")
            url = getattr(f, "url", "")
            lines.append(f"- [{idx}] {source} {url}".strip())
    return lines
