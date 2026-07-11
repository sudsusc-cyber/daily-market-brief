"""
Frontier Labs importance filter.

This is intentionally strict: OpenAI / Anthropic render only when a major
development changes a holding-chain judgment.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher

from src.collectors.frontier_labs import FrontierBundle, FrontierItem, SourceType
from src.config import HOLDINGS
from src.processors.html_safe import is_safe_url
from src.processors.llm_client import LLMClient

logger = logging.getLogger(__name__)

_ALLOWED_RELATED_TICKERS = {h.ticker for h in HOLDINGS} | {"AMD"}


@dataclass
class FrontierKeyPoint:
    lab: str
    text: str
    related_tickers: list[str]
    source_url: str
    source_name: str
    score: int
    source_type: SourceType = "google_news"
    published_at: datetime | None = None


_TASK_INSTRUCTION = """\
任务:判断下面 OpenAI / Anthropic 候选新闻是否属于"前沿模型"重大进展。

这个模块不是 AI 新闻流。只回答一个问题:
过去 24 小时,这家前沿模型实验室是否出现了会实质影响开源持仓链判断的重大变化?

当前可关联的持仓链 ticker 只包括:
MSFT, COST, AAPL, NVDA, TSM, MCO, GOOG, BRK.B, KO, AXP, 0700.HK, 9992.HK, AMD

必须满足:
- 是 OpenAI 或 Anthropic 自身的重大进展,不是泛泛 AI 新闻
- 对上述一个或多个 ticker 有清晰影响路径
- 能改变对云、GPU、先进制程、模型商业化、企业采用、AI capex、监管/法律风险的判断

高质量进展:
- 新一代模型发布,足以改变竞争格局或算力需求
- 重大融资、收入、商业化、企业采用披露
- 重大云、芯片、数据中心、算力合作
- AI capex / 训练成本 / 推理成本的实质信息
- 安全、治理、监管、法律变化,且有清晰持仓链影响
- 对 MSFT / GOOG / NVDA / TSM / AMD 等有明确影响

低质量进展一律 no:
- 小产品更新、开发者工具噪音、微小 benchmark
- KOL / 分析师评论、社媒争议、人员八卦
- 未证实融资传闻
- 品牌宣传、泛泛说"AI 很重要"
- 与持仓链无清晰关系的 OpenAI / Anthropic 普通新闻

跨来源合并:
- 多个来源描述同一件事必须合并成一行
- 主索引取信息最完整、来源最权威的一条
- 来源优先级:官方 > Reuters > Bloomberg > Financial Times / WSJ > CNBC > 其他

输出严格按以下格式,不要前言、解释或 markdown:
▦ N: yes | score=5 | tickers=MSFT,NVDA,TSM | 一句中文摘要
▦ N,M: yes | score=4 | tickers=MSFT,GOOG | 合并后的一句中文摘要
▦ N: no | score=2 | 淘汰原因

硬约束:
- yes 行必须包含 score=1..5 和 tickers=...
- score < 4 即使 yes 也不会展示
- tickers 为空或不在允许清单中不会展示
- 中文摘要 24-44 字,客观、克制,不要写"重大""重磅"
- 摘要不要重复实验室名称,展示层会自动加 OpenAI / Anthropic
- 每个输入索引必须只出现一次
"""


_LINE_RE = re.compile(
    r"^▦\s*([\d,\s]+?)\s*:\s*(yes|no)\s*"
    r"(?:\|\s*score\s*=\s*(\d)\s*)?"
    r"(?:\|\s*tickers\s*=\s*([^|]+?)\s*)?"
    r"\|\s*(.+?)\s*$",
    re.IGNORECASE,
)

_TICKER_ALIASES = {
    "GOOGL": "GOOG",
    "BRK-B": "BRK.B",
}


def _format_input(items: list[FrontierItem]) -> str:
    lines: list[str] = []
    for i, item in enumerate(items, start=1):
        snippet = (item.snippet or "").strip()
        if len(snippet) > 220:
            snippet = snippet[:220].rstrip() + "..."
        line = f"▦ {i}: 标题={item.title}"
        if snippet:
            line += f" / 摘要={snippet}"
        line += f" / 来源={item.source} / 类型={item.source_type}"
        lines.append(line)
    return "\n".join(lines)


def _normalize(text: str) -> str:
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"[,。!?:;、—\-()()【】《》\"\"'']", "", text)
    return text.lower()


def _similar(a: str, b: str, threshold: float = 0.62) -> bool:
    return SequenceMatcher(None, _normalize(a), _normalize(b)).ratio() >= threshold


def _parse_tickers(raw: str | None) -> list[str]:
    if not raw:
        return []
    tickers: list[str] = []
    for token in re.split(r"[,，、;；/／\s]+", raw.upper()):
        ticker = token.strip().strip("()[]{}<>\"'“”‘’")
        if not ticker:
            continue
        ticker = _TICKER_ALIASES.get(ticker, ticker)
        if ticker in _ALLOWED_RELATED_TICKERS and ticker not in tickers:
            tickers.append(ticker)
    return tickers


def _clean_summary(text: str) -> str:
    text = (text or "").strip().strip("\"'“”「」 ")
    text = re.sub(r"^(?:OpenAI|Anthropic)\s*[：:｜|-]\s*", "", text, flags=re.IGNORECASE)
    if len(text) > 58:
        text = text[:57].rstrip(" ,，。;；") + "..."
    return text


def _parse_output(text: str, items: list[FrontierItem], lab: str) -> list[FrontierKeyPoint]:
    kept: list[FrontierKeyPoint] = []
    for line in text.splitlines():
        match = _LINE_RE.match(line.strip())
        if not match:
            continue
        idx_part = match.group(1).strip()
        verdict = match.group(2).lower()
        score_raw = (match.group(3) or "").strip()
        tickers = _parse_tickers(match.group(4))
        body = _clean_summary(match.group(5))

        if verdict != "yes":
            continue
        try:
            score = int(score_raw)
        except (TypeError, ValueError):
            logger.info("frontier_labs_filter.missing_score lab=%s text=%s", lab, body[:60])
            continue
        if score < 4 or score > 5:
            logger.info(
                "frontier_labs_filter.low_or_invalid_score lab=%s score=%s text=%s",
                lab,
                score,
                body[:60],
            )
            continue
        if not tickers:
            logger.info("frontier_labs_filter.missing_tickers lab=%s text=%s", lab, body[:60])
            continue

        primary_idx: int | None = None
        for token in idx_part.split(","):
            try:
                value = int(token.strip())
            except ValueError:
                continue
            if 1 <= value <= len(items):
                primary_idx = value
                break
        if primary_idx is None:
            continue
        if not body:
            continue
        if any(_similar(body, existing.text) for existing in kept):
            continue

        source_item = items[primary_idx - 1]
        if not is_safe_url(source_item.url):
            logger.warning(
                "frontier_labs_filter.dropped_unsafe_url lab=%s url=%r",
                lab,
                (source_item.url or "")[:80],
            )
            continue

        kept.append(
            FrontierKeyPoint(
                lab=lab,
                text=body,
                related_tickers=tickers,
                source_url=source_item.url,
                source_name=source_item.source,
                score=score,
                source_type=source_item.source_type,
                published_at=source_item.published_at,
            )
        )
    return kept


_SOURCE_AUTHORITY: dict[str, int] = {
    "official": 0,
    "Reuters": 1,
    "Bloomberg": 2,
    "Financial Times": 3,
    "FT": 3,
    "Wall Street Journal": 4,
    "WSJ": 4,
    "CNBC": 5,
}


def select_frontier_items(
    items: list[FrontierKeyPoint],
    *,
    max_total: int = 2,
    max_items_per_lab: int = 1,
) -> list[FrontierKeyPoint]:
    def sort_key(item: FrontierKeyPoint) -> tuple:
        ts = item.published_at.timestamp() if item.published_at else 0
        authority = 0 if item.source_type == "official" else _SOURCE_AUTHORITY.get(item.source_name, 6)
        return (-item.score, authority, -ts)

    selected: list[FrontierKeyPoint] = []
    by_lab: dict[str, int] = {}
    for item in sorted(items, key=sort_key):
        if by_lab.get(item.lab, 0) >= max_items_per_lab:
            continue
        selected.append(item)
        by_lab[item.lab] = by_lab.get(item.lab, 0) + 1
        if len(selected) >= max_total:
            break
    return selected


def filter_one(
    bundle: FrontierBundle,
    *,
    client: LLMClient,
    max_items: int = 8,
) -> list[FrontierKeyPoint]:
    if not bundle.items:
        return []
    items = bundle.items[:max_items]
    payload = _format_input(items)
    if not payload.strip():
        return []
    try:
        resp = client.chat(
            payload,
            task_extra=_TASK_INSTRUCTION,
            max_tokens=6000,
            temperature=0.2,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("frontier_labs_filter.failed lab=%s exc=%s", bundle.lab, exc)
        return []
    if not resp.text:
        logger.warning("frontier_labs_filter.failed lab=%s reason=%s", bundle.lab, resp.error)
        return []
    kept = _parse_output(resp.text, items, bundle.lab)
    logger.info(
        "frontier_labs_filter.ok lab=%s candidates=%d kept=%d",
        bundle.lab,
        len(items),
        len(kept),
    )
    return kept


def filter_all(
    bundles: list[FrontierBundle],
    *,
    client: LLMClient,
    max_items_per_lab: int = 8,
) -> list[FrontierKeyPoint]:
    points: list[FrontierKeyPoint] = []
    for bundle in bundles:
        points.extend(filter_one(bundle, client=client, max_items=max_items_per_lab))
    return select_frontier_items(points)
