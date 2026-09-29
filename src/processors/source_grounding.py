"""Deterministic source-to-publication binding for Chinese publication.

Unrestricted multilingual paraphrases cannot be strictly proven with regexes.
Only complete source sentences or separately checked complete-sentence translations
are eligible. Unsupported rewrites fall back to a checked translation or a complete
Chinese source excerpt. Untranslated English is retained for audit, not publication.
Translation checks are bounded lexical checks, not complete semantic proof.
An exact substring alone is insufficient (it can omit 'not' or change scope).
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import asdict, dataclass

from src.processors.html_safe import is_safe_url
from src.processors.news_presentation import publication_text
from src.processors.news_selection import (
    chinese_prose,
    complete_excerpt,
    factual_excerpt,
    old_event_excerpt,
    old_event_recap,
    plain_source,
    promotional_prose,
    sentences,
)
from src.processors.translation_guard import translation_errors
from src.utils.news_facts import canonical_fact, source_text

logger = logging.getLogger(__name__)
INSTRUCTION = "\n事实约束：正文选用所引来源的完整片段译文，或完整中文原文句子；可组合多条，但不要自由改写或补充结论。保留否定、数字、单位、日期、对象和状态。片段译文与不可变原文单独提供，引用编号必须匹配。"


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
    validated_text: str
    source_name: str
    presentation_version: int = 1


def source_sentences(item) -> list[str]:
    if old_event_recap(item):
        return []
    result = []
    for key in ("title", "summary", "snippet"):
        raw = plain_source(str(getattr(item, key, "") or "")).strip()
        if not raw:
            continue
        # No splitting on semicolon/colon: their clauses often qualify a claim.
        result.extend(s for s in [raw, *sentences(raw)]
                      if complete_excerpt(s, getattr(item, 'source', '')) and not old_event_excerpt(item, s) and not promotional_prose(s))
    return list(dict.fromkeys(result))


def checked_excerpt(item) -> tuple[str, str]:
    """A translated excerpt must be an entire sentence in immutable source fields."""
    excerpt = plain_source(str(getattr(item, "source_excerpt", "") or getattr(item, "title", "")))
    translated = plain_source(str(getattr(item, "translated_excerpt", "") or getattr(item, "translated_title", "")))
    if (excerpt in source_sentences(item) and chinese_prose(translated) and complete_excerpt(translated, getattr(item, 'source', ''))
            and translated != excerpt
            and not translation_errors(excerpt, translated)):
        return excerpt, translated
    return "", ""


def source_prompt(item) -> str:
    excerpt, translated = checked_excerpt(item)
    raw = excerpt or factual_excerpt(item)
    return f"完整证据片段={raw} / 可刊发译文={translated or '无（仅完整中文原文可刊；英文不可直接刊出，不得编造）'}"


def grounded_text(claim: str, items: list) -> tuple[str, list[dict]]:
    claim = plain_source(claim).strip()
    candidates = [
        item for item in items if is_safe_url(getattr(item, "url", "")) and source_text(item) and not old_event_recap(item)
    ]
    if not candidates:
        return "", []
    selected = []
    for item in candidates:
        for sentence in source_sentences(item):
            if sentence == plain_source(item.title) and factual_excerpt(item) != sentence:
                continue
            if canonical_fact(sentence) == canonical_fact(claim):
                selected = [(item, sentence, "verified_extract")]
                break
        excerpt, translated = checked_excerpt(item)
        if not selected and translated and canonical_fact(translated) == canonical_fact(claim):
            selected = [(item, excerpt, "checked_translation")]
        if selected:
            break
    if not selected:
        # This is a visible downgrade, not a declaration that a translation or
        # free-form summary has passed a lexical/LLM self-check.
        selected = [
            (item, checked_excerpt(item)[0] or factual_excerpt(item), "checked_translation" if checked_excerpt(item)[1] else "source_extract")
            for item in candidates
            if str(getattr(item, "title", "")).strip()
        ]
        logger.warning("news.source_extract_fallback candidates=%d", len(selected))
    if not selected:
        return "", []
    evidence = []
    outputs = []
    for item, excerpt, mode in selected:
        original = source_text(item)
        # Excerpts come from complete source fields/sentences after HTML removal.
        if excerpt not in plain_source(original) or old_event_excerpt(item, excerpt):
            continue
        validated = checked_excerpt(item)[1] if mode == "checked_translation" else excerpt
        source_name = str(getattr(item, "source", "") or "")
        displayed = publication_text(validated, source_name=source_name)
        if not chinese_prose(displayed) or not complete_excerpt(displayed) or promotional_prose(displayed):
            logger.warning("news.publication_rejected reason=not_complete_chinese")
            continue
        if mode == "source_extract":
            mode = "verified_extract"
        outputs.append(displayed)
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
                    output_text=displayed,
                    mode=mode,
                    validated_text=validated,
                    source_name=source_name,
                )
            )
        )
    output = "；".join(dict.fromkeys(outputs))
    return (output, evidence) if evidence else ("", [])
