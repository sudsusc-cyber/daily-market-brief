"""
LLM evidence 抽取器。

输入：当天已通过日报筛选的内容 + active themes 列表
输出：list[ThesisEvidence]（校验后，含 evidence_id，含 canonicalized theme）

不直接做文件 IO。写入由 state.py 统一负责。
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date
from typing import Any
from urllib.parse import urldefrag

from src.config import HOLDINGS
from src.processors.html_safe import is_safe_url, strip_all_tags
from src.processors.llm_client import LLMClient

from .models import ThesisEvidence
from .prompts import MAX_EVIDENCE_ITEMS, SYSTEM_EXTRA, build_user_prompt
from .theme_taxonomy import (
    canonicalize_theme as _canonicalize_theme,
)
from .theme_taxonomy import (
    make_evidence_id as _make_evidence_id,
)
from .theme_taxonomy import (
    normalize_text as _normalize_text,
)

logger = logging.getLogger("thesis.extractor")

_MAX_EXTRACT_ATTEMPTS = 2

_VALID_DIRECTIONS = {"support", "risk", "neutral", "new_variable"}
_VALID_HORIZONS = {"quarterly", "multi_year", "structural"}
_VALID_SOURCE_SECTIONS = {"company_news", "macro", "voices", "berkshire", "frontier_labs"}
_VALID_TICKERS = {holding.ticker for holding in HOLDINGS} | {"AMD"}
_TICKER_ALIASES = {"GOOGL": "GOOG", "BRK-B": "BRK.B"}
_REQUIRED_FIELDS = {
    "source_section", "source_name", "related_tickers", "theme",
    "direction", "strength", "horizon", "text", "why_it_matters",
}


def _value(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _normalize_grounding_text(value: Any) -> str:
    """忽略标点与空白做保守的逐字摘录校验。"""
    text = strip_all_tags(str(value or ""))
    return "".join(ch.lower() for ch in text if ch.isalnum())


def _normalize_grounding_url(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw or not is_safe_url(raw):
        return ""
    return urldefrag(raw)[0].rstrip("/")


def _grounding_material(
    *,
    company_news: Any | None,
    macro_news: Any | None,
    figure_summaries: list[Any] | None,
    frontier_labs_events: list[Any] | None,
) -> dict[str, dict[str, Any]]:
    """收集最终邮件会展示的事实正文与来源链接。"""
    material: dict[str, dict[str, Any]] = {}

    def add(section: str, text: Any, urls: list[Any]) -> None:
        normalized_text = _normalize_grounding_text(text)
        normalized_urls = {
            url for raw in urls if (url := _normalize_grounding_url(raw))
        }
        if not normalized_text or not normalized_urls:
            return
        bucket = material.setdefault(section, {"text": "", "urls": set()})
        bucket["text"] += f"|{normalized_text}|"
        bucket["urls"].update(normalized_urls)

    def add_summary(section: str, summary: Any | None) -> None:
        if not summary:
            return
        if hasattr(summary, "summary_html"):
            footnotes = _value(summary, "footnotes", []) or []
            add(
                section,
                _value(summary, "summary_html", ""),
                [_value(item, "url", "") for item in footnotes],
            )
            return
        # 兼容旧调用形态；生产路径使用上面的已筛选 summary。
        texts: list[str] = []
        urls: list[str] = []
        for bundle in summary if isinstance(summary, (list, tuple)) else []:
            for item in _value(bundle, "items", []) or []:
                texts.extend([
                    str(_value(item, "title", "") or ""),
                    str(_value(item, "summary", "") or ""),
                ])
                urls.append(str(_value(item, "url", "") or ""))
        add(section, " ".join(texts), urls)

    add_summary("company_news", company_news)
    add_summary("macro", macro_news)

    voice_texts: list[str] = []
    voice_urls: list[str] = []
    for summary in figure_summaries or []:
        for item in _value(summary, "items", []) or []:
            voice_texts.append(str(_value(item, "text", "") or ""))
            voice_urls.append(str(_value(item, "source_url", "") or ""))
    add("voices", " ".join(voice_texts), voice_urls)

    frontier_texts: list[str] = []
    frontier_urls: list[str] = []
    for item in frontier_labs_events or []:
        frontier_texts.append(str(_value(item, "text", "") or ""))
        frontier_urls.append(str(
            _value(item, "url", "") or _value(item, "source_url", "") or ""
        ))
    add("frontier_labs", " ".join(frontier_texts), frontier_urls)
    return material


def _filter_grounded_evidence(
    evidence: list[ThesisEvidence],
    material: dict[str, dict[str, Any]],
) -> list[ThesisEvidence]:
    """拒绝无法在最终邮件正文和脚注中同时核对的模型输出。"""
    grounded: list[ThesisEvidence] = []
    for item in evidence:
        bucket = material.get(item.source_section)
        normalized_text = _normalize_grounding_text(item.text)
        normalized_url = _normalize_grounding_url(item.url)
        if not bucket:
            logger.warning(
                "extractor.skip_ungrounded_section theme=%s section=%s",
                item.theme, item.source_section,
            )
            continue
        if len(normalized_text) < 8 or normalized_text not in bucket["text"]:
            logger.warning(
                "extractor.skip_ungrounded_text theme=%s section=%s",
                item.theme, item.source_section,
            )
            continue
        if not normalized_url or normalized_url not in bucket["urls"]:
            logger.warning(
                "extractor.skip_ungrounded_url theme=%s section=%s",
                item.theme, item.source_section,
            )
            continue
        grounded.append(item)
    logger.info(
        "extractor.grounded total=%d accepted=%d", len(evidence), len(grounded),
    )
    return grounded

# ─── theme canonicalization ───────────────────────────────────────


def canonicalize_theme(raw: str) -> str:
    """规整 theme key，并合并生产账本中已确认的近义主题。"""
    return _canonicalize_theme(raw)


# ─── evidence_id ──────────────────────────────────────────────────


def normalize_text(text: str) -> str:
    """collapse whitespace + strip + lowercase，用于 evidence_id 稳定性。"""
    return _normalize_text(text)


def make_evidence_id(ev: ThesisEvidence) -> str:
    return _make_evidence_id(ev)


# ─── JSON parsing ─────────────────────────────────────────────────


def _parse_response_with_status(raw: str | None) -> tuple[list[dict[str, Any]], bool]:
    """解析 LLM 响应并返回 (items, JSON 是否有效)；有效的 [] 与失败分开。"""
    if not raw:
        return [], False
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
            return [], False

    if isinstance(result, list):
        return result, True
    if isinstance(result, dict):
        if "items" in result and isinstance(result["items"], list):
            return result["items"], True
        return [result], True
    return [], False


def parse_response(raw: str | None) -> list[dict[str, Any]]:
    """兼容既有调用：解析失败与有效空数组均返回空 list。"""
    items, _ = _parse_response_with_status(raw)
    return items


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
        if strength > 5:
            logger.warning("extractor.skip_bad_strength index=%d strength=%d", i, strength)
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
        normalized_tickers: list[str] = []
        for raw_ticker in tickers:
            ticker = _TICKER_ALIASES.get(str(raw_ticker).strip().upper(), str(raw_ticker).strip().upper())
            if ticker in _VALID_TICKERS and ticker not in normalized_tickers:
                normalized_tickers.append(ticker)
        if not normalized_tickers:
            logger.warning("extractor.skip_no_valid_tickers index=%d", i)
            continue

        # canonicalize theme
        theme = canonicalize_theme(str(obj.get("theme", "")))
        if not theme or len(theme) > 80 or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", theme):
            logger.warning("extractor.skip_bad_theme index=%d val=%r", i, theme)
            continue

        source_section = str(obj.get("source_section", "")).strip()
        if source_section not in _VALID_SOURCE_SECTIONS:
            logger.warning("extractor.skip_bad_source_section index=%d val=%r", i, source_section)
            continue
        source_name = str(obj.get("source_name", "")).strip()[:120]
        text = str(obj.get("text", "")).strip()[:500]
        why_it_matters = str(obj.get("why_it_matters", "")).strip()[:500]
        if not source_name or not text or not why_it_matters:
            logger.warning("extractor.skip_empty_content index=%d", i)
            continue
        raw_url = obj.get("url")
        url = str(raw_url).strip() if raw_url else None
        if url and not is_safe_url(url):
            logger.warning("extractor.skip_unsafe_url index=%d", i)
            continue

        ev = ThesisEvidence(
            evidence_id="",  # 下面填入
            date=today_str,
            source_section=source_section,
            source_name=source_name,
            url=url,
            related_tickers=normalized_tickers,
            theme=theme,
            direction=direction,  # type: ignore[arg-type]
            strength=strength,
            horizon=horizon,  # type: ignore[arg-type]
            text=text,
            why_it_matters=why_it_matters,
        )
        ev.evidence_id = make_evidence_id(ev)
        result.append(ev)

    logger.info("extractor.validated total=%d accepted=%d", len(raw_items), len(result))
    return result


# ─── main entry ───────────────────────────────────────────────────


def extract_with_status(
    *,
    client: LLMClient,
    company_news: Any | None = None,
    macro_news: Any | None = None,
    figure_summaries: list[Any] | None = None,
    berkshire_events: Any | None = None,
    frontier_labs_events: list[Any] | None = None,
    active_themes: list[str] | None = None,
    today: date | None = None,
) -> tuple[list[ThesisEvidence], str | None]:
    """
    从已筛选内容中抽取长期判断 evidence。

    - extractor 不做文件 IO，返回 (evidence, error)
    - 有效空数组返回 ([], None)；处理失败返回 ([], error)
    """
    if today is None:
        today = date.today()
    today_str = today.isoformat()

    # Active Themes 只是映射提示，不能在没有当日展示内容时单独触发模型。
    material = _grounding_material(
        company_news=company_news,
        macro_news=macro_news,
        figure_summaries=figure_summaries,
        frontier_labs_events=frontier_labs_events,
    )
    if not material:
        logger.info("extractor.empty_grounded_input")
        return [], None

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
        return [], None

    logger.info("extractor.call user_prompt_chars=%d", len(user_prompt))
    last_error: str | None = None
    for attempt in range(1, _MAX_EXTRACT_ATTEMPTS + 1):
        task_extra = SYSTEM_EXTRA
        if attempt > 1:
            task_extra += """

上一次输出为空、不是有效 JSON，或所有非空条目均未通过字段校验。
这次只输出有效 JSON 数组；若确实没有可记录证据，输出 []。
"""
        resp = client.chat(
            user_prompt=user_prompt,
            task_extra=task_extra,
            max_tokens=3000,
            temperature=0.1,
            timeout=30,
            thinking=False,
        )

        if resp.error or resp.text is None:
            last_error = resp.error or "EmptyOutput"
            logger.warning(
                "extractor.attempt_failed attempt=%d/%d error=%s",
                attempt, _MAX_EXTRACT_ATTEMPTS, last_error,
            )
            continue

        raw_items, valid_json = _parse_response_with_status(resp.text)
        if not valid_json:
            last_error = "InvalidJSONOutput"
            logger.warning(
                "extractor.invalid_output attempt=%d/%d reason=%s",
                attempt, _MAX_EXTRACT_ATTEMPTS, last_error,
            )
            continue

        if not raw_items:
            logger.info("extractor.no_evidence attempt=%d", attempt)
            return [], None

        evidence = validate_and_build(raw_items, today_str)
        evidence = _filter_grounded_evidence(evidence, material)
        if not evidence:
            last_error = "AllNonEmptyItemsFailedValidation"
            logger.warning(
                "extractor.invalid_output attempt=%d/%d reason=%s",
                attempt, _MAX_EXTRACT_ATTEMPTS, last_error,
            )
            continue
        if len(evidence) > MAX_EVIDENCE_ITEMS:
            logger.info(
                "extractor.cap_evidence total=%d kept=%d",
                len(evidence), MAX_EVIDENCE_ITEMS,
            )
            evidence = evidence[:MAX_EVIDENCE_ITEMS]
        return evidence, None

    logger.warning(
        "extractor.llm_failed attempts=%d error=%s",
        _MAX_EXTRACT_ATTEMPTS, last_error or "unknown",
    )
    return [], last_error or "UnknownProcessingFailure"


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
    """兼容既有调用；需要失败状态的主流程使用 extract_with_status。"""
    evidence, _ = extract_with_status(
        client=client,
        company_news=company_news,
        macro_news=macro_news,
        figure_summaries=figure_summaries,
        berkshire_events=berkshire_events,
        frontier_labs_events=frontier_labs_events,
        active_themes=active_themes,
        today=today,
    )
    return evidence
