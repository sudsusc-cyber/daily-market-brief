"""Conservative factual identity: similarity proposes, literal facts decide.

Unknown paraphrases remain candidates. We do not assert semantic equivalence
from embeddings, a model verdict, or a high edit-distance score.
"""

import html
import re
import unicodedata

from bs4 import BeautifulSoup


def canonical_fact(text: str) -> str:
    text = BeautifulSoup(html.unescape(text or ""), "html.parser").get_text(" ")
    text = unicodedata.normalize("NFKC", text).casefold()
    text = re.sub(r"^\s*(?:breaking|update)\s*:\s*", "", text)
    # Preserve digits, signs, decimal separators, units and word boundaries.
    # Punctuation between Chinese words is cosmetic; English spaces are not.
    text = re.sub(r"[，。！!；;：:\"“”‘’]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text.rstrip(".").strip()


def equivalent(a: str, b: str) -> bool:
    left, right = canonical_fact(a), canonical_fact(b)
    return bool(left and left == right)


def source_text(item) -> str:
    # Never consult translated_title when establishing source facts.
    return "\n".join(
        str(getattr(item, key, "") or "") for key in ("title", "summary", "snippet", "source_body")
    ).strip()


def content_key(item) -> str:
    title = getattr(item, "title", "") or ""
    source = getattr(item, "source", "") or ""
    # Remove only an exact known publisher suffix, never an arbitrary clause.
    for dash in (" - ", " — ", " – "):
        suffix = dash + source
        if source and title.endswith(suffix):
            title = title[: -len(suffix)]
    return (
        canonical_fact(title)
        + "|"
        + canonical_fact(getattr(item, "summary", "") or getattr(item, "snippet", "") or "")
    )
