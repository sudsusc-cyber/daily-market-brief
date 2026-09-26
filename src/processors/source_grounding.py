"""Deterministic source-to-publication binding, with an extractive fallback.

Unrestricted multilingual paraphrases cannot be strictly proven with regexes.
Therefore only complete, verbatim source sentences/titles are published as
verified facts; unsupported rewrites degrade to the cited original title.
An exact substring alone is insufficient (it can omit 'not' or change scope).
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import asdict, dataclass

from src.processors.html_safe import is_safe_url, strip_all_tags
from src.utils.news_facts import canonical_fact, source_text

logger = logging.getLogger(__name__)
INSTRUCTION = "\n事实约束：仅摘录所引原始标题或摘要中的完整句子，保留否定、数字、单位、日期、对象和状态。译文仅供选稿参考，不作为证据。不要把未发生写成已发生。"


@dataclass(frozen=True)
class SourceEvidence:
    url: str
    original_title: str
    original_summary: str
    excerpt: str
    published_at: str
    source_sha256: str
    output_text: str
    mode: str


def source_sentences(item) -> list[str]:
    result = []
    for key in ("title", "summary", "snippet"):
        raw = strip_all_tags(str(getattr(item, key, "") or "")).strip()
        if not raw:
            continue
        # No splitting on semicolon/colon: their clauses often qualify a claim.
        result.append(raw)
        result.extend(
            s.strip() for s in re.split(r"(?<=[。！？])|(?<=[.!?])\s+(?=[A-Z])", raw) if s.strip()
        )
    return list(dict.fromkeys(result))


def grounded_text(claim: str, items: list) -> tuple[str, list[dict]]:
    claim = strip_all_tags(claim).strip()
    candidates = [
        item for item in items if is_safe_url(getattr(item, "url", "")) and source_text(item)
    ]
    if not candidates:
        return "", []
    selected = []
    for item in candidates:
        for sentence in source_sentences(item):
            if canonical_fact(sentence) == canonical_fact(claim):
                selected = [(item, sentence, "verified_extract")]
                break
        if selected:
            break
    if not selected:
        # This is a visible downgrade, not a declaration that a translation or
        # free-form summary has passed a lexical/LLM self-check.
        selected = [
            (item, strip_all_tags(item.title).strip(), "source_extract")
            for item in candidates
            if str(getattr(item, "title", "")).strip()
        ]
        logger.warning("news.source_extract_fallback candidates=%d", len(selected))
    if not selected:
        return "", []
    output = "；".join(dict.fromkeys(sentence for _, sentence, _ in selected))
    if any(mode == "source_extract" for _, _, mode in selected):
        output = "原文摘录：" + output
    evidence = []
    for item, excerpt, mode in selected:
        original = source_text(item)
        # Excerpts come from complete source fields/sentences after HTML removal.
        if excerpt not in strip_all_tags(original):
            continue
        evidence.append(
            asdict(
                SourceEvidence(
                    url=item.url,
                    original_title=item.title,
                    original_summary=str(
                        getattr(item, "summary", "") or getattr(item, "snippet", "") or ""
                    ),
                    excerpt=excerpt,
                    published_at=str(getattr(item, "published_at", "") or ""),
                    source_sha256=hashlib.sha256(original.encode()).hexdigest(),
                    output_text=excerpt,
                    mode=mode,
                )
            )
        )
    return (output, evidence) if evidence else ("", [])
