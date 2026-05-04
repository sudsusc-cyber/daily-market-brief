"""
LLM evidence 抽取器。

输入：当天已通过日报筛选的内容 + active themes 列表
输出：list[ThesisEvidence]（校验后，含 evidence_id，含 canonicalized theme）

不直接做文件 IO。写入由 state.py 统一负责。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import date
from typing import Any

from src.processors.llm_client import LLMClient

from .models import ThesisEvidence
from .prompts import MAX_EVIDENCE_ITEMS, SYSTEM_EXTRA, build_user_prompt

logger = logging.getLogger("thesis.extractor")

_VALID_DIRECTIONS = {"support", "risk", "neutral", "new_variable"}
_VALID_HORIZONS = {"quarterly", "multi_year", "structural"}
_REQUIRED_FIELDS = {
    "source_section", "source_name", "related_tickers", "theme",
    "direction", "strength", "horizon", "text", "why_it_matters",
}

# 公司前缀剥离清单
_COMPANY_PREFIXES = (
    "openai-", "anthropic-", "msft-", "goog-", "nvda-",
    "tsm-", "aapl-", "cost-", "mco-", "ko-", "axp-", "brk-",
)


# ─── theme canonicalization ───────────────────────────────────────


def canonicalize_theme(raw: str) -> str:
    """规整 LLM 生成的 theme key（表层规整，不做语义聚类）。"""
    s = raw.strip().lower()
    s = re.sub(r"[\s_]+", "-", s)  # 空格/下划线 → 连字符
    s = re.sub(r"-+", "-", s)      # 连续连字符合并
    s = s.strip("-")

    # 公司前缀剥离
    for prefix in _COMPANY_PREFIXES:
        if s.startswith(prefix):
            s = s[len(prefix):]
            break

    return s


# ─── evidence_id ──────────────────────────────────────────────────


def normalize_text(text: str) -> str:
    """collapse whitespace + strip + lowercase，用于 evidence_id 稳定性。"""
    return re.sub(r"\s+", " ", text.strip()).lower()


def make_evidence_id(ev: ThesisEvidence) -> str:
    parts = [
        ev.date,
        ev.source_section,
        ev.source_name,
        ev.url or "",
        ev.theme,
        normalize_text(ev.text),
    ]
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


# ─── JSON parsing ─────────────────────────────────────────────────


def parse_response(raw: str | None) -> list[dict[str, Any]]:
    """解析 LLM 响应为 dict 列表。失败 → 空 list。"""
    if not raw:
        return []
    text = raw.strip()

    # 1) 剥 ```json / ``` fence
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if m:
        text = m.group(1).strip()

    # 2) 尝试直接解析
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        # 3) 用 raw_decode 找到第一个完整 JSON 对象/数组
        decoder = json.JSONDecoder()
        for start_char in ("[", "{"):
            idx = text.find(start_char)
            if idx == -1:
                continue
            try:
                result, _ = decoder.raw_decode(text, idx)
            except json.JSONDecodeError:
                continue
            break
        else:
            logger.warning("extractor.parse_failed raw_preview=%s", text[:200])
            return []

    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        if "items" in result and isinstance(result["items"], list):
            return result["items"]
        return [result]
    return []


# ─── validation + build ───────────────────────────────────────────


def validate_and_build(
    raw_items: list[dict[str, Any]],
    today_str: str,
) -> list[ThesisEvidence]:
    """逐条校验、canonicalize、填 evidence_id。不合格的丢弃并 log。"""
    result: list[ThesisEvidence] = []
    for i, obj in enumerate(raw_items):
        if not isinstance(obj, dict):
            logger.warning("extractor.skip_not_dict index=%d", i)
            continue

        missing = [f for f in _REQUIRED_FIELDS if f not in obj]
        if missing:
            logger.warning("extractor.skip_missing_fields index=%d missing=%s", i, missing)
            continue

        try:
            strength = int(obj["strength"])
        except (ValueError, TypeError):
            logger.warning("extractor.skip_bad_strength index=%d", i)
            continue
        if strength < 3:
            logger.info("extractor.skip_weak index=%d strength=%d", i, strength)
            continue

        direction = obj.get("direction", "")
        if direction not in _VALID_DIRECTIONS:
            logger.warning("extractor.skip_bad_direction index=%d val=%r", i, direction)
            continue

        horizon = obj.get("horizon", "")
        if horizon not in _VALID_HORIZONS:
            logger.warning("extractor.skip_bad_horizon index=%d val=%r", i, horizon)
            continue

        tickers = obj.get("related_tickers", [])
        if not isinstance(tickers, list):
            tickers = []

        # canonicalize theme
        theme = canonicalize_theme(str(obj.get("theme", "")))

        ev = ThesisEvidence(
            evidence_id="",  # 下面填入
            date=today_str,
            source_section=str(obj.get("source_section", "")),
            source_name=str(obj.get("source_name", "")),
            url=obj.get("url") if obj.get("url") else None,
            related_tickers=tickers,
            theme=theme,
            direction=direction,  # type: ignore[arg-type]
            strength=strength,
            horizon=horizon,  # type: ignore[arg-type]
            text=str(obj.get("text", "")),
            why_it_matters=str(obj.get("why_it_matters", "")),
        )
        ev.evidence_id = make_evidence_id(ev)
        result.append(ev)

    logger.info("extractor.validated total=%d accepted=%d", len(raw_items), len(result))
    return result


# ─── main entry ───────────────────────────────────────────────────


def extract(
    *,
    client: LLMClient,
    company_news: Any | None = None,
    macro_news: Any | None = None,
    figure_summaries: list[Any] | None = None,
    berkshire_events: Any | None = None,
    frontier_labs_events: list[Any] | None = None,
    active_themes: list[str] | None = None,
    today: date | None = None,
) -> list[ThesisEvidence]:
    """
    从已筛选内容中抽取长期判断 evidence。

    - extractor 不做文件 IO，仅 return list[ThesisEvidence]
    - 任何失败 → log warning + 返回空 list，不抛异常
    """
    if today is None:
        today = date.today()
    today_str = today.isoformat()

    user_prompt = build_user_prompt(
        company_news=company_news,
        macro_news=macro_news,
        figure_summaries=figure_summaries,
        berkshire_events=berkshire_events,
        frontier_labs_events=frontier_labs_events,
        active_themes=active_themes,
    )

    if user_prompt.strip().startswith("（今日无") and len(user_prompt) < 50:
        logger.info("extractor.empty_input")
        return []

    logger.info("extractor.call user_prompt_chars=%d", len(user_prompt))
    resp = client.chat(
        user_prompt=user_prompt,
        task_extra=SYSTEM_EXTRA,
        max_tokens=2048,
        temperature=0.2,
    )

    if resp.error or resp.text is None:
        logger.warning("extractor.llm_failed error=%s", resp.error)
        return []

    raw_items = parse_response(resp.text)
    evidence = validate_and_build(raw_items, today_str)
    if len(evidence) > MAX_EVIDENCE_ITEMS:
        logger.info(
            "extractor.cap_evidence total=%d kept=%d",
            len(evidence), MAX_EVIDENCE_ITEMS,
        )
        evidence = evidence[:MAX_EVIDENCE_ITEMS]
    return evidence
