from __future__ import annotations

from datetime import UTC, datetime

from src.valuation.freshness import (
    check_official_freshness,
    evaluate_freshness,
    latest_relevant_hkex_document,
    latest_relevant_sec_document,
)
from src.valuation.models import OfficialDocument
from src.valuation.policy import POLICIES


def _recent() -> dict:
    return {
        "form": ["4", "8-K", "10-Q"],
        "items": ["", "2.02,9.01", ""],
        "accessionNumber": ["insider", "earnings", "quarter"],
        "acceptanceDateTime": [
            "2026-08-27T18:30:30.000Z",
            "2026-08-26T20:30:28.000Z",
            "2026-08-25T10:01:02.000Z",
        ],
        "reportDate": ["", "2026-06-30", "2026-06-30"],
        "primaryDocument": ["x.xml", "earnings.htm", "quarter.htm"],
        "primaryDocDescription": ["FORM 4", "8-K", "10-Q"],
    }


def test_sec_selection_ignores_newer_insider_filing() -> None:
    now = datetime(2026, 8, 28, tzinfo=UTC)
    document = latest_relevant_sec_document(
        ticker="AAPL",
        cik="0000320193",
        recent=_recent(),
        discovered_at=now,
    )
    assert document is not None
    assert document.document_id == "earnings"
    assert document.document_type == "8-K"
    assert document.source_domain == "sec.gov"


def test_ordinary_8k_does_not_invalidate_valuation() -> None:
    recent = _recent()
    recent["items"][1] = "5.02,9.01"
    document = latest_relevant_sec_document(
        ticker="AAPL",
        cik="0000320193",
        recent=recent,
        discovered_at=datetime(2026, 8, 28, tzinfo=UTC),
    )
    assert document is not None
    assert document.document_id == "quarter"


def test_foreign_issuer_6k_is_reviewed() -> None:
    recent = _recent()
    recent["form"][1] = "6-K"
    recent["items"][1] = ""
    document = latest_relevant_sec_document(
        ticker="TSM",
        cik="0001046179",
        recent=recent,
        discovered_at=datetime(2026, 8, 28, tzinfo=UTC),
    )
    assert document is not None
    assert document.document_id == "earnings"


def test_freshness_blocks_mismatched_document() -> None:
    now = datetime(2026, 8, 28, tzinfo=UTC)
    document = OfficialDocument(
        document_id="new",
        document_type="10-Q",
        report_period="2026-06-30",
        published_at=now,
        discovered_at=now,
        source_url="https://www.sec.gov/example",
        source_domain="sec.gov",
    )
    result = evaluate_freshness(
        policy=POLICIES["AAPL"],
        latest_document=document,
        valuation_document_id="old",
        checked_at=now,
    )
    assert result.status == "new_filing_pending"
    assert not result.may_publish_value


def test_freshness_allows_exact_document_match() -> None:
    now = datetime(2026, 8, 28, tzinfo=UTC)
    document = OfficialDocument(
        document_id="same",
        document_type="10-Q",
        report_period="2026-06-30",
        published_at=now,
        discovered_at=now,
        source_url="https://www.sec.gov/example",
        source_domain="sec.gov",
    )
    result = evaluate_freshness(
        policy=POLICIES["AAPL"],
        latest_document=document,
        valuation_document_id="same",
        checked_at=now,
    )
    assert result.status == "current"
    assert result.may_publish_value


def test_hkex_parser_uses_latest_results_not_monthly_return() -> None:
    html = """
    <table>
      <tr>
        <td class="release-time">Release Time: 27/08/2026 18:00</td>
        <td><div class="headline">Monthly Returns</div>
          <div class="doc-link"><a href="/monthly.pdf">Monthly Return</a></div></td>
      </tr>
      <tr>
        <td class="release-time">Release Time: 20/08/2026 16:31</td>
        <td><div class="headline">Announcements and Notices - [Interim Results]</div>
          <div class="doc-link"><a href="/listedco/2026082000353.pdf">
          INTERIM RESULTS ANNOUNCEMENT FOR THE SIX MONTHS ENDED 30 JUNE 2026
          </a></div></td>
      </tr>
    </table>
    """
    document = latest_relevant_hkex_document(
        ticker="9992.HK",
        html=html,
        discovered_at=datetime(2026, 8, 28, tzinfo=UTC),
    )
    assert document is not None
    assert document.document_id == "2026082000353"
    assert document.report_period == "2026-06-30"
    assert document.published_at.isoformat() == "2026-08-20T08:31:00+00:00"


def test_final_check_reuses_hash_only_for_same_document(monkeypatch) -> None:
    now = datetime(2026, 8, 28, tzinfo=UTC)
    first_document = OfficialDocument(
        document_id="same",
        document_type="10-Q",
        report_period="2026-06-30",
        published_at=now,
        discovered_at=now,
        source_url="https://www.sec.gov/example",
        source_domain="sec.gov",
        content_hash="verified-hash",
    )
    first = evaluate_freshness(
        policy=POLICIES["AAPL"],
        latest_document=first_document,
        valuation_document_id="same",
        checked_at=now,
    )
    final_document = OfficialDocument(
        document_id="same",
        document_type="10-Q",
        report_period="2026-06-30",
        published_at=now,
        discovered_at=now,
        source_url="https://www.sec.gov/example",
        source_domain="sec.gov",
    )
    monkeypatch.setattr(
        "src.valuation.freshness.fetch_latest_sec_document",
        lambda *args, **kwargs: final_document,
    )
    results = check_official_freshness(
        [POLICIES["AAPL"]],
        valuation_document_ids={"AAPL": "same"},
        checked_at=now,
        download_original=False,
        prior_results={"AAPL": first},
    )
    assert results["AAPL"].status == "current"
    assert results["AAPL"].latest_document is not None
    assert results["AAPL"].latest_document.content_hash == "verified-hash"
