"""Commit candidate hashes only when their source actually appears in the edition."""

from collections.abc import Callable, Iterable

from bs4 import BeautifulSoup


def candidate_sources(bundles: Iterable, scope: Callable, content_hash: Callable) -> dict[str, set[str]]:
    # Capture before translation mutates the source title used by content_hash.
    result: dict[str, set[str]] = {}
    for bundle in bundles:
        for item in bundle.items:
            result.setdefault(content_hash(scope(bundle), item), set()).add(item.url)
    return result


def summary_urls(summary) -> set[str]:
    if summary is None:
        return set()
    soup = BeautifulSoup(summary.summary_html, "html.parser")
    return {str(a["href"]) for a in soup.select("a[href]")}


def published_pending(pending: dict[str, str], candidates: dict[str, set[str]],
                      published_urls: set[str]) -> dict[str, str]:
    # Old entries are retained; only new candidates are restricted by publication.
    return {key: value for key, value in pending.items()
            if key not in candidates or candidates[key] & published_urls}
