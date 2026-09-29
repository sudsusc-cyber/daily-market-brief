"""
Frontier Labs importance filter.

This is intentionally strict: OpenAI / Anthropic render only when a major
development changes a holding-chain judgment.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime

from src.collectors.frontier_labs import FrontierBundle, FrontierItem, SourceType
from src.config import HOLDINGS
from src.processors.html_safe import is_safe_url
from src.processors.llm_client import LLMClient
from src.processors.news_selection import frontier_candidate
from src.processors.source_grounding import INSTRUCTION, grounded_text, source_prompt
from src.utils.news_facts import content_key, equivalent
from src.utils.secrets import redact_secrets

logger = logging.getLogger(__name__)

_ALLOWED_RELATED_TICKERS = {h.ticker for h in HOLDINGS} | {"AMD"}
_MAX_FILTER_ATTEMPTS = 2


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
    evidence: list[dict] = field(default_factory=list)


@dataclass
class FrontierFilterReport:
    items: list[FrontierKeyPoint] = field(default_factory=list)
    source_failures: list[str] = field(default_factory=list)
    processing_failures: list[str] = field(default_factory=list)
    content_rejections: list[str] = field(default_factory=list)

    @property
    def failures(self) -> list[str]:
        return self.source_failures + self.processing_failures + self.content_rejections

    @property
    def state(self) -> str:
        if self.items:
            return "partial" if self.failures else "published"
        if self.processing_failures:
            return "processing_failed"
        if self.source_failures:
            return "source_unavailable"
        return "content_rejected" if self.content_rejections else "silent"

    @property
    def fallback_note(self) -> str | None:
        return {
            "processing_failed": "前沿动态筛选暂不可用，本期暂不刊载。",
            "source_unavailable": "前沿动态来源读取失败，本期暂不刊载。",
            "content_rejected": "前沿动态候选内容未通过核验，本期暂不刊载。",
        }.get(self.state)

    def health(self) -> dict:
        return {"state": self.state, "source_failures": len(self.source_failures),
                "processing_failures": len(self.processing_failures),
                "content_rejections": len(self.content_rejections),
                "fallback": bool(self.fallback_note), "silence": self.state == "silent"}


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
- yes 正文直接选用输入中的完整可刊发译文或完整中文证据句，不压缩改写，不补充推论
- 完整句子的事实与限定优先于字数；不得为了缩短而删掉否定、条件或主体
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
        line = f"▦ {i}: 标题={item.title} / {source_prompt(item)}"
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
    return equivalent(a, b)


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
    return text


@dataclass
class _FrontierParseResult:
    items: list[FrontierKeyPoint]
    covered_indexes: set[int]
    duplicate_indexes: set[int]
    invalid_yes: bool = False
    rejected_indexes: dict[int, str] = field(default_factory=dict)

    def is_complete(self, expected_count: int) -> bool:
        return (
            self.covered_indexes == set(range(1, expected_count + 1))
            and not self.duplicate_indexes
            and not self.invalid_yes
        )


def _parse_output_result(
    text: str,
    items: list[FrontierItem],
    lab: str,
) -> _FrontierParseResult:
    kept: list[tuple[int, FrontierKeyPoint]] = []
    index_counts: dict[int, int] = {}
    invalid_yes = False
    rejected_indexes: dict[int, str] = {}
    for line in text.splitlines():
        match = _LINE_RE.match(line.strip())
        if not match:
            continue
        idx_part = match.group(1).strip()
        verdict = match.group(2).lower()
        score_raw = (match.group(3) or "").strip()
        tickers = _parse_tickers(match.group(4))
        body = _clean_summary(match.group(5))

        valid_indexes: list[int] = []
        for token in idx_part.split(","):
            try:
                value = int(token.strip())
            except ValueError:
                continue
            if 1 <= value <= len(items):
                valid_indexes.append(value)
                index_counts[value] = index_counts.get(value, 0) + 1
        if not valid_indexes:
            if verdict == "yes":
                invalid_yes = True
            continue

        if verdict != "yes":
            continue
        try:
            score = int(score_raw)
        except (TypeError, ValueError):
            logger.info("frontier_labs_filter.missing_score lab=%s text=%s", lab, body[:60])
            invalid_yes = True
            continue
        if score < 4 or score > 5:
            logger.info(
                "frontier_labs_filter.low_or_invalid_score lab=%s score=%s text=%s",
                lab,
                score,
                body[:60],
            )
            if score < 1 or score > 5:
                invalid_yes = True
            continue
        if not tickers:
            logger.info("frontier_labs_filter.missing_tickers lab=%s text=%s", lab, body[:60])
            invalid_yes = True
            continue

        # A model merging indexes does not prove that their facts are equal.
        for source_index in valid_indexes:
            source_item = items[source_index - 1]
            if not is_safe_url(source_item.url):
                rejected_indexes[source_index] = "unsafe_source_url"
                continue
            if not frontier_candidate(source_item):
                continue
            supported, mapping = grounded_text(body, [source_item])
            if not supported:
                rejected_indexes[source_index] = "no_verified_chinese_excerpt"
                continue
            kept.append((source_index, FrontierKeyPoint(
                lab=lab, related_tickers=tickers, source_type=source_item.source_type,
                text=supported, evidence=mapping,
                source_url=source_item.url, source_name=source_item.source,
                score=score, published_at=source_item.published_at,
            )))
    duplicates = {index for index, count in index_counts.items() if count > 1}
    unique = []
    seen_source_facts: set[str] = set()
    for index, point in kept:
        key = content_key(items[index - 1])
        if index not in duplicates and key not in seen_source_facts:
            unique.append(point)
            seen_source_facts.add(key)
    return _FrontierParseResult(
        items=unique,
        covered_indexes=set(index_counts),
        duplicate_indexes=duplicates,
        invalid_yes=invalid_yes,
        rejected_indexes=rejected_indexes,
    )


def _parse_output(text: str, items: list[FrontierItem], lab: str) -> list[FrontierKeyPoint]:
    """兼容既有测试和调用的纯解析入口。"""
    return _parse_output_result(text, items, lab).items


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


def _filter_one_report(
    bundle: FrontierBundle,
    *,
    client: LLMClient,
    max_items: int = 8,
) -> FrontierFilterReport:
    report = FrontierFilterReport(source_failures=[
        f"{bundle.lab}: SourceError: {redact_secrets(str(error))[:240]}" for error in bundle.errors
    ])
    if not bundle.items:
        return report
    items = [item for item in bundle.items if frontier_candidate(item)][:max_items]
    payload = _format_input(items)
    if not payload.strip():
        return report

    last_error: str | None = None
    for attempt in range(1, _MAX_FILTER_ATTEMPTS + 1):
        instruction = _TASK_INSTRUCTION + INSTRUCTION
        if attempt > 1:
            instruction += """

【重试修正】上一次输出为空、格式错误或遗漏输入编号。这次每个输入编号必须
恰好出现一次；直接输出 ▦ 行，不要解释或 markdown。
"""
        try:
            resp = client.chat(
                payload,
                task_extra=instruction,
                max_tokens=2000,
                temperature=0.1,
                timeout=30,
                thinking=False,
            )
        except Exception as exc:  # noqa: BLE001
            last_error = f"{type(exc).__name__}: {redact_secrets(str(exc))[:160]}"
            logger.warning(
                "frontier_labs_filter.attempt_failed lab=%s attempt=%d/%d reason=%s",
                bundle.lab, attempt, _MAX_FILTER_ATTEMPTS, last_error,
            )
            continue
        if not resp.text:
            last_error = redact_secrets(str(resp.error or "EmptyOutput"))[:240]
            logger.warning(
                "frontier_labs_filter.attempt_failed lab=%s attempt=%d/%d reason=%s",
                bundle.lab, attempt, _MAX_FILTER_ATTEMPTS, last_error,
            )
            continue

        parsed = _parse_output_result(resp.text, items, bundle.lab)
        # A rejected source is a per-candidate publication decision, not a
        # malformed model response. Never discard its verified neighbours.
        if parsed.covered_indexes:
            report.items = parsed.items
            report.content_rejections = [
                f"{bundle.lab}: index={index} reason={reason}"
                for index, reason in sorted(parsed.rejected_indexes.items())
            ]
        for rejection in report.content_rejections:
            logger.warning("frontier_labs_filter.content_rejected %s", rejection)
        if parsed.is_complete(len(items)):
            logger.info(
                "frontier_labs_filter.ok lab=%s candidates=%d kept=%d attempt=%d",
                bundle.lab, len(items), len(parsed.items), attempt,
            )
            return report

        last_error = (
            "IncompleteOrInvalidOutput: "
            f"covered={sorted(parsed.covered_indexes)}/{len(items)} "
            f"duplicates={sorted(parsed.duplicate_indexes)} invalid_yes={parsed.invalid_yes}"
        )
        logger.warning(
            "frontier_labs_filter.invalid_output lab=%s attempt=%d/%d reason=%s",
            bundle.lab, attempt, _MAX_FILTER_ATTEMPTS, last_error,
        )

    logger.warning(
        "frontier_labs_filter.failed lab=%s attempts=%d reason=%s",
        bundle.lab, _MAX_FILTER_ATTEMPTS, last_error or "unknown",
    )
    report.processing_failures.append(f"{bundle.lab}: {last_error or 'UnknownProcessingFailure'}")
    return report


def _filter_one_with_status(
    bundle: FrontierBundle, *, client: LLMClient, max_items: int = 8,
) -> tuple[list[FrontierKeyPoint], str | None]:
    report = _filter_one_report(bundle, client=client, max_items=max_items)
    return report.items, "; ".join(report.failures) or None


def filter_one(
    bundle: FrontierBundle,
    *,
    client: LLMClient,
    max_items: int = 8,
) -> list[FrontierKeyPoint]:
    items, _ = _filter_one_with_status(bundle, client=client, max_items=max_items)
    return items


def filter_all(
    bundles: list[FrontierBundle],
    *,
    client: LLMClient,
    max_items_per_lab: int = 8,
) -> list[FrontierKeyPoint]:
    items, _ = filter_all_with_status(
        bundles,
        client=client,
        max_items_per_lab=max_items_per_lab,
    )
    return items


def filter_all_with_status(
    bundles: list[FrontierBundle],
    *,
    client: LLMClient,
    max_items_per_lab: int = 8,
) -> tuple[list[FrontierKeyPoint], list[str]]:
    report = filter_all_report(bundles, client=client, max_items_per_lab=max_items_per_lab)
    return report.items, report.failures


def filter_all_report(
    bundles: list[FrontierBundle], *, client: LLMClient, max_items_per_lab: int = 8,
) -> FrontierFilterReport:
    report = FrontierFilterReport()
    for bundle in bundles:
        result = _filter_one_report(
            bundle,
            client=client,
            max_items=max_items_per_lab,
        )
        report.items.extend(result.items)
        report.source_failures.extend(result.source_failures)
        report.processing_failures.extend(result.processing_failures)
        report.content_rejections.extend(result.content_rejections)
    report.items = select_frontier_items(report.items)
    logger.info("frontier_labs_filter.publication health=%s", report.health())
    return report
