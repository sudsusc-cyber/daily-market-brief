"""Private publication artifacts: exact rendered bodies and allowlisted metadata.

The archive never receives settings, SMTP envelopes, credentials or API headers.
Delivery and content quality have independent lifecycles.
"""

import hashlib
import json
import logging
import math
import os
import re
from collections import Counter
from datetime import date
from pathlib import Path

from src.sender.smtp_sender import _html_to_plain

logger = logging.getLogger(__name__)


def _atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    temporary.replace(path)


def content_report(
    *, signals, valuations, sentiment, news: dict, expected_tickers, diagnostics=None,
    section_health=None, expected_prices=(), expected_metrics=(),
) -> dict:
    rows = []
    for ticker in expected_tickers:
        item = (valuations or {}).get(ticker)
        status = (
            "missing"
            if item is None or item.is_pending
            else (
                "carried"
                if (item.status != "current" or item.historical_reference or item.data_note)
                else "current_verified"
            )
        )
        conflict = "冲突" in json.dumps((diagnostics or {}).get(ticker, {}), ensure_ascii=False)
        if item and (
            any("冲突" in warning or "mismatch" in warning for warning in item.warnings)
            or item.is_pending
            and conflict
        ):
            status = "conflict"
        rows.append(
            {
                "section": "valuation",
                "key": ticker,
                "status": status,
                "observed_at": item.financial_as_of if item else None,
                "verified_at": item.verified_at if item else None,
                "source": item.source_url if item else None,
            }
        )
    for signal in signals:
        rows.append(
            {
                "section": "prices",
                "key": signal.holding.ticker,
                "status": (
                    "missing" if signal.error or not signal.observed_at or not _finite(signal.last_close)
                    else "current_verified"
                ),
                "observed_at": signal.observed_at,
                "source": signal.data_source,
            }
        )
    for metric in (sentiment.metrics if sentiment else []):
        rows.append(
            {
                "section": "sentiment",
                "key": metric.name,
                "status": (
                    "missing"
                    if metric.error or not _finite(metric.current) or not metric.observed_at
                    else "carried" if metric.stale_from else "current_verified"
                ),
                "observed_at": metric.observed_at,
                "fetched_at": metric.fetched_at,
                "current": metric.current if _finite(metric.current) else None,
                "prior": metric.prior if _finite(metric.prior) else None,
                "unit": metric.unit,
                "source": metric.source,
            }
        )
    # A collector timeout can return an empty bundle. Absence must not erase the
    # denominator and make the entire section look verified.
    for section, expected in (("prices", expected_prices), ("sentiment", expected_metrics)):
        present = {row["key"] for row in rows if row["section"] == section}
        rows.extend({"section": section, "key": key, "status": "missing",
                     "observed_at": None, "source": None}
                    for key in expected if key not in present)
    coverage, mappings = {}, {}
    for section, pair in news.items():
        candidates, published = pair
        evidence = []
        for obj in published:
            evidence.extend(getattr(obj, "evidence", []) or [])
        coverage[section] = {
            "candidates": candidates,
            "published_sources": len({e["url"] for e in evidence}),
            "extractive_fallbacks": sum(e.get("mode") == "source_extract" for e in evidence),
            "checked_translations": sum(e.get("mode") == "checked_translation" for e in evidence),
            "non_chinese_outputs": sum(not re.search(r"[一-鿿]", str(e.get("output_text", ""))) for e in evidence),
            "translation_validation": "numbers_units_entities_modality_event_states; not_full_semantic_proof",

        }
        mappings[section] = evidence
    raw_counts = Counter(row["status"] for row in rows)
    counts = {
        key: raw_counts[key] for key in ("current_verified", "carried", "missing", "conflict")
    }
    section_health = section_health or {}
    degraded = any(counts.get(key, 0) for key in ("carried", "missing", "conflict")) or any(
        row["candidates"] and (
            (not row["published_sources"] and not section_health.get(section, {}).get("silence"))
            or row["extractive_fallbacks"] or row["non_chinese_outputs"])
        for section, row in coverage.items()
    ) or (sentiment is not None and not sentiment.metrics) or any(
        any(health.get(key) for key in ("source_failures", "processing_failures", "fallback", "timeout_count"))
        for health in section_health.values()
    )
    return {
        "status": "degraded" if degraded else "verified",
        "counts": counts,
        "observations": rows,
        "news_coverage": coverage,
        "summary_mapping": mappings,
        "diagnostics": diagnostics or {},
        "section_health": section_health,
    }


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def archive_publication(html: str, *, generated_at, report: dict, inline_images=()) -> Path:
    root = Path(os.environ.get("BRIEF_AUDIT_DIR", ".brief-audit"))
    run_id = os.environ.get("GH_RUN_ID", "local")
    attempt = os.environ.get("GH_RUN_ATTEMPT", os.environ.get("GITHUB_RUN_ATTEMPT", "1"))
    if not all(re.fullmatch(r"[\w-]+", value) for value in (run_id, attempt)):
        raise ValueError("invalid audit execution identity")
    edition = generated_at.date().isoformat()
    directory = root / f"{run_id}-{attempt}-{edition}"
    directory.mkdir(parents=True, exist_ok=True)
    plain = _html_to_plain(html)
    (directory / "email.html").write_text(html, encoding="utf-8")
    (directory / "email.txt").write_text(plain, encoding="utf-8")
    preview = html
    image_manifest = []
    for image in inline_images:
        data = image.path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        # Public inline images only; never serialize sender settings or envelopes.
        suffix = image.path.suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}:
            raise ValueError("unsupported audit image type")
        relative = f"images/{digest}{suffix}"
        (directory / "images").mkdir(exist_ok=True)
        (directory / relative).write_bytes(data)
        preview = preview.replace(f"cid:{image.cid}", relative)
        image_manifest.append({"cid": image.cid, "path": relative, "sha256": digest})
    (directory / "preview.html").write_text(preview, encoding="utf-8")
    report = {**report, "html_bytes": len(html.encode())}
    if report["html_bytes"] > 98304:
        report["status"] = "degraded"
    manifest = {
        "run_id": run_id,
        "run_attempt": attempt,
        "edition": edition,
        "generated_at": generated_at.isoformat(),
        "delivery": {"status": "not_sent"},
        "images": image_manifest,
        "content": report,
        "sha256": {
            "html": hashlib.sha256(html.encode()).hexdigest(),
            "text": hashlib.sha256(plain.encode()).hexdigest(),
        },
    }
    _atomic_json(directory / "manifest.json", manifest)
    logger.info(
        "quality.content status=%s counts=%s news=%s html_bytes=%s",
        report["status"],
        report["counts"],
        report["news_coverage"],
        report["html_bytes"],
    )
    return directory


def archive_delivery(directory: Path, receipt: dict) -> Path:
    path = directory / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["delivery"] = {
        key: receipt.get(key)
        for key in ("status", "accepted_count", "refused_count", "sent_at", "edition")
    }
    # Edition is the actual SMTP acceptance date, including cross-midnight runs.
    edition = date.fromisoformat(receipt.get("edition", manifest["edition"])).isoformat()
    manifest["edition"] = edition
    _atomic_json(path, manifest)
    expected = directory.with_name(f"{manifest['run_id']}-{manifest['run_attempt']}-{edition}")
    if expected != directory:
        directory.rename(expected)
        directory = expected
    return directory
