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

from src.processors.announcement_context import (
    action_context,
    action_context_date,
    requires_action_context,
)
from src.processors.editorial_evidence import analysis_source, editorial_issue
from src.processors.html_safe import is_safe_url
from src.processors.news_presentation import PRESENTATION_VERSION, present
from src.processors.news_selection import (
    chinese_prose,
    complete_excerpt,
    factual_excerpt,
    old_event_recap,
    plain_source,
    promotional_prose,
    publication_candidates,
    publishable_excerpt,
)
from src.processors.technical_context import contextual_excerpt
from src.processors.translation_guard import translation_errors
from src.utils.news_facts import canonical_fact, source_text

logger = logging.getLogger(__name__)
INSTRUCTION = "\n选稿与语言分开：按原文事实及重要性选稿，不能因尚无中文译文淘汰。可引用完整原句或已核验译文，程序负责翻译和刊发核验。不要自由改写或补充结论；保留否定、数字、单位、日期、对象和状态，引用编号必须匹配。"


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
    presentation_version: int = PRESENTATION_VERSION
    presentation_operations: tuple[str, ...] = ()
    publication_path: str = "selected_excerpt"
    source_kind: str = "reported"
    source_body: str = ""
    source_published_at: str = ""
    context_url: str = ""
    context_fetched_at: str = ""
    event_date: str = ""
    event_date_basis: str = ""


def source_sentences(item) -> list[str]:
    if old_event_recap(item):
        return []
    context = contextual_excerpt(item)
    result = [context] if context and publishable_excerpt(item, context) else []
    for key in ("title", "summary", "snippet", "source_body"):
        raw = plain_source(str(getattr(item, key, "") or "")).strip()
        if not raw:
            continue
        # No splitting on semicolon/colon: their clauses often qualify a claim.
        result.extend(s for s in [raw, *publication_candidates(item, raw)]
                      if publishable_excerpt(item, s))
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
    return f"完整证据片段={raw} / 译文参考={translated or '待翻译；请按原文选稿'}"


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
                excerpt, translated = checked_excerpt(item)
                # Selection may quote the English original. Use its independently
                # checked translation; an exact original match must not bypass
                # that translation and fail the final Chinese presentation gate.
                mode = "checked_translation" if translated and excerpt == sentence else "verified_extract"
                selected = [(item, sentence, mode)]
                break
        excerpt, translated = checked_excerpt(item)
        if not selected and translated and canonical_fact(translated) == canonical_fact(claim):
            selected = [(item, excerpt, "checked_translation")]
        if selected:
            break
    fallback = not selected
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
        if excerpt not in plain_source(original) or not publishable_excerpt(item, excerpt):
            continue
        validated = checked_excerpt(item)[1] if mode == "checked_translation" else excerpt
        source_name = str(getattr(item, "source", "") or "")
        presentation = present(validated, source_name=source_name, original_text=excerpt)
        displayed = presentation.text
        if not chinese_prose(displayed) or not complete_excerpt(displayed) or promotional_prose(displayed) or editorial_issue(displayed):
            logger.warning("news.publication_rejected reason=not_complete_chinese")
            continue
        if mode == "source_extract":
            mode = "verified_extract"
        outputs.append(displayed)
        evidence.append(
            asdict(
                SourceEvidence(
                    url=item.url,
                    event_date=action_context_date(item, excerpt),
                    event_date_basis="dated_source_excerpt" if action_context_date(item, excerpt) else "",
                    source_body=str(getattr(item, "source_body", "") or ""),
                    source_published_at=str(getattr(item, "source_published_at", "") or ""),
                    context_url=str(getattr(item, "context_url", "") or ""),
                    context_fetched_at=str(getattr(item, "context_fetched_at", "") or ""),
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
                    presentation_operations=presentation.operations,
                    publication_path="source_fallback" if fallback else "selected_excerpt",
                    source_kind="analysis" if analysis_source(item.title, str(getattr(item, "summary", "") or "")) else "reported",
                )
            )
        )
    output = "；".join(dict.fromkeys(outputs))
    return (output, evidence) if evidence else ("", [])


def diagnostic_text(value, limit=1800):
    """Redact first, then bound diagnostics (never clip a secret before redacting)."""
    import re

    from src.utils.secrets import redact_secrets

    value = redact_secrets(plain_source(str(value or "")))
    value = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[email redacted]", value)
    return value[:limit]


def publication_diagnostic(item) -> dict:
    """Bounded, redacted evidence for a rejected candidate, never mail content."""
    excerpt = factual_excerpt(item)
    translated = str(getattr(item, "translated_excerpt", "") or getattr(item, "translated_title", ""))
    errors = []
    if requires_action_context(item) and not action_context(item):
        errors.append("recap_requires_dated_action_context")
    if not excerpt:
        errors.append("no_publishable_source_excerpt")
    elif not chinese_prose(excerpt) and not checked_excerpt(item)[1]:
        errors.extend(translation_errors(excerpt, translated) if translated else ["missing_translation"])
    candidate = present(checked_excerpt(item)[1] or excerpt, source_name=getattr(item, "source", ""), original_text=excerpt).text
    if candidate:
        if not chinese_prose(candidate):
            errors.append("not_chinese")
        if not complete_excerpt(candidate):
            errors.append("incomplete_excerpt")
        if promotional_prose(candidate):
            errors.append("promotional_prose")
        issue = editorial_issue(candidate)
        if issue:
            errors.append(issue)
    return {
        "url": diagnostic_text(getattr(item, "url", ""), 2000),
        "title": diagnostic_text(getattr(item, "title", "")),
        "snippet": diagnostic_text(getattr(item, "snippet", "") or getattr(item, "summary", "")),
        "source": diagnostic_text(getattr(item, "source", ""), 100),
        "published_at": diagnostic_text(getattr(item, "published_at", ""), 80),
        "source_published_at": diagnostic_text(getattr(item, "source_published_at", ""), 80),
        "source_excerpt": diagnostic_text(excerpt),
        "translated_excerpt": diagnostic_text(translated),
        "errors": list(dict.fromkeys(errors)),
        "translation_rejection": diagnostic_text(getattr(item, "translation_diagnostic", {}), 1000),
        "speaker_context": diagnostic_text(getattr(item, "speaker_context_diagnostic", ""), 100),
        "truncated": any(len(str(value or "")) > 1800 for value in (excerpt, translated, getattr(item, "title", ""), getattr(item, "snippet", ""))),
    }


def recover_selected_translations(items, *, client, audit: list, limit=8) -> bool:
    """One bounded translation batch for selected originals, never new selection."""
    from src.processors.translator import translate_in_place_news

    pending = []
    seen = set()
    for item in items:
        excerpt = factual_excerpt(item)
        if (id(item) not in seen and excerpt and not chinese_prose(excerpt)
                and not checked_excerpt(item)[1] and is_safe_url(item.url)):
            pending.append(item)
            seen.add(id(item))
    pending = pending[:limit]
    if not pending:
        return False
    record = {"phase": "selected_translation", "before": [publication_diagnostic(i) for i in pending]}
    audit.append(record)
    try:
        translate_in_place_news(pending, client=client, max_attempts=1, timeout=20)
    except Exception as exc:
        record["error"] = diagnostic_text(type(exc).__name__, 100)
    record["after"] = [publication_diagnostic(i) for i in pending]
    return True
