from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from src.valuation.engine import ValuationInputError, ValuationSnapshot
from src.valuation.models import OfficialDocument
from src.valuation.official_review import review_snapshot
from src.valuation.policy import POLICIES


def _baseline() -> ValuationSnapshot:
    policy = POLICIES["AAPL"]
    return ValuationSnapshot(
        ticker="AAPL",
        formula_id=policy.formula_id,
        model_version=policy.model_version,
        method=policy.method,
        source_document_id="doc",
        source_url="https://www.sec.gov/doc",
        source_content_hash="hash",
        financial_as_of="2026-06-30",
        approved_at="2026-08-28",
        currency_symbol="$",
        data_provider="provider",
        data_retrieved_at="2026-08-28T00:00:00+00:00",
        normalization_version="v1",
        discount_rate=policy.hurdle_rate,
        terminal_growth=policy.terminal_growth,
        cash_flows_per_share=(10.0, 10.5, 11.0, 11.5, 12.0),
    )


def _document() -> OfficialDocument:
    now = datetime(2026, 8, 28, tzinfo=UTC)
    return OfficialDocument(
        document_id="doc",
        document_type="10-Q",
        report_period="2026-06-30",
        published_at=now,
        discovered_at=now,
        source_url="https://www.sec.gov/doc",
        source_domain="sec.gov",
        content_hash="hash",
    )


class _Client:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.prompt = ""

    def chat(self, prompt: str, **_kwargs):
        self.prompt = prompt
        return SimpleNamespace(text=json.dumps(self.payload), error=None)


def _payload(snapshot: ValuationSnapshot) -> dict:
    return {
        "status": "ok",
        "snapshot": asdict(snapshot),
        "citations": [
            {
                "field": "Free Cash Flow",
                "location": "Cash flows",
                "quote": "cash generated from operations",
            },
            {
                "field": "Shares",
                "location": "EPS note",
                "quote": "diluted weighted average shares",
            },
        ],
    }


def test_official_review_accepts_pinned_cited_snapshot(monkeypatch) -> None:
    html = b"<html><body>" + (b"official financial statement cash flow shares " * 30) + b"</body></html>"
    monkeypatch.setattr(
        "src.valuation.official_review._download_verified",
        lambda _document: (html, "text/html"),
    )
    baseline = _baseline()
    client = _Client(_payload(baseline))
    reviewed = review_snapshot(
        baseline=baseline,
        policy=POLICIES["AAPL"],
        document=_document(),
        client=client,
    )
    assert reviewed == baseline
    assert "<official_document>" in client.prompt
    assert "不得修改" in client.prompt


def test_official_review_rejects_formula_override(monkeypatch) -> None:
    html = b"<html><body>" + (b"official financial statement cash flow shares " * 30) + b"</body></html>"
    monkeypatch.setattr(
        "src.valuation.official_review._download_verified",
        lambda _document: (html, "text/html"),
    )
    baseline = _baseline()
    payload = _payload(baseline)
    payload["snapshot"]["discount_rate"] = 0.08
    with pytest.raises(ValuationInputError, match="冻结字段 discount_rate"):
        review_snapshot(
            baseline=baseline,
            policy=POLICIES["AAPL"],
            document=_document(),
            client=_Client(payload),
        )
