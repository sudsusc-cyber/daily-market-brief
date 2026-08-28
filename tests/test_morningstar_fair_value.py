from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from src.valuation.morningstar import (
    SECURITIES,
    MorningstarFairValue,
    MorningstarPublicProvider,
    _Candidate,
    _extract_value,
    _parse_company_report_candidates,
    load_cache,
    refresh_fair_values,
)


class _Response:
    def __init__(
        self,
        text: str,
        *,
        ok: bool = True,
        status_code: int | None = None,
    ) -> None:
        self.text = text
        self.ok = ok
        self.status_code = status_code if status_code is not None else (200 if ok else 503)
        self.headers: dict[str, str] = {}

    def raise_for_status(self) -> None:
        if not self.ok:
            raise RuntimeError(f"HTTP {self.status_code}")


class _Session:
    def __init__(self, responses: list[_Response]) -> None:
        self.responses = iter(responses)
        self.headers: dict[str, str] = {}

    def get(self, *_args, **_kwargs) -> _Response:
        return next(self.responses)


def _value(
    ticker: str = "AAPL",
    *,
    fair_value: float = 290.0,
    updated: str = "2026-08-07",
    retrieved: str = "2026-08-28T00:00:00+00:00",
) -> MorningstarFairValue:
    security = SECURITIES[ticker]
    return MorningstarFairValue(
        ticker=ticker,
        provider_code=security.provider_code,
        fair_value=fair_value,
        currency=security.currency,
        rating_type="published-research",
        fair_value_updated_at=updated,
        retrieved_at=retrieved,
        source_provider="Morningstar public research",
        source_url="https://www.morningstar.com/stocks/apple-test",
    )


class _Provider:
    def __init__(self, values=None, failures=None) -> None:
        self.values = values or {}
        self.failures = failures or {}

    def fetch_all(self, securities, *, checked_at):
        return self.values, self.failures


def test_extracts_tsm_adr_value_not_twd_company_report_value() -> None:
    text = """
Title: Taiwan Semiconductor Earnings
## Key Morningstar Metrics for Taiwan Semiconductor Manufacturing
* Fair Value Estimate: $534.00
The bottom line: TWD 3,440 per local share and $534 per ADR.
"""
    assert _extract_value(text, SECURITIES["TSM"]) == (534.0, "USD")


def test_parses_latest_official_company_report_listing() -> None:
    text = """
### [Tencent Earnings](http://www.morningstar.com/company-reports/1494726-test?listing=x)

Summary.

Ivan Su Aug 12, 2026
"""
    candidates = _parse_company_report_candidates(text)
    assert candidates == [
        _Candidate(
            "https://www.morningstar.com/company-reports/1494726-test?listing=x",
            datetime(2026, 8, 12, tzinfo=UTC),
        )
    ]


def test_extracts_berkshire_class_b_not_class_a() -> None:
    text = """
Title: Berkshire Hathaway Outlook
We keep our $765,000 ($510) per Class A (B) share fair value estimates.
"""
    assert _extract_value(text, SECURITIES["BRK.B"]) == (510.0, "USD")


def test_scopes_multi_company_article_to_linde() -> None:
    text = """
Title: Basic Materials Sector Picks
### Ecolab [ECL]
* Fair Value Estimate: $300.00
### Linde [LIN]
* Fair Value Estimate: $540.00
### Corteva [CTVA]
* Fair Value Estimate: $90.00
"""
    assert _extract_value(text, SECURITIES["LIN"]) == (540.0, "USD")


def test_reader_recovers_when_direct_page_is_only_a_shell() -> None:
    reader_text = """
Title: After Earnings, Is Apple Stock a Buy?
Published Time: 2026-08-07T12:00:00Z
Morningstar Apple research
* Fair Value Estimate: $285.00
"""
    provider = MorningstarPublicProvider(
        reader_min_interval=0,
        session=_Session(
            [
                _Response("<html><title>Apple | Morningstar</title><body>Subscribe</body></html>"),
                _Response(reader_text),
            ]
        )
    )
    result = provider._read(
        _Candidate("https://www.morningstar.com/stocks/apple-test", None),
        SECURITIES["AAPL"],
    )
    assert result.fair_value == 285.0
    assert result.source_provider.endswith("via Jina Reader")


def test_reader_retries_temporary_rate_limit(monkeypatch) -> None:
    reader_text = """
Title: After Earnings, Is Apple Stock a Buy?
Published Time: 2026-08-07T12:00:00Z
Morningstar Apple research
* Fair Value Estimate: $285.00
"""
    monkeypatch.setattr("src.valuation.morningstar.time.sleep", lambda _seconds: None)
    provider = MorningstarPublicProvider(
        reader_min_interval=0,
        session=_Session(
            [
                _Response("<html><body>Subscribe</body></html>"),
                _Response("rate limited", ok=False, status_code=429),
                _Response(reader_text),
            ]
        ),
    )
    result = provider._read(
        _Candidate("https://www.morningstar.com/stocks/apple-test", None),
        SECURITIES["AAPL"],
    )
    assert result.fair_value == 285.0


def test_hk_curated_value_uses_latest_official_research_page() -> None:
    security = SECURITIES["0700.HK"]
    page = """
Title: Tencent Earnings: AI Spending Weighs on Near-Term Cash Flow
Published Time: 2026-08-12T08:00:00Z
Morningstar research for Tencent Holdings
"""
    provider = MorningstarPublicProvider(
        reader_min_interval=0,
        session=_Session([_Response(page)]),
    )
    result = provider._read(
        _Candidate(
            security.curated_urls[0],
            datetime(2026, 8, 12, tzinfo=UTC),
            security.curated_values[0],
        ),
        security,
    )
    assert result.fair_value == 780.0
    assert result.currency == "HKD"
    assert result.fair_value_updated_at == "2026-08-12"


def test_hk_does_not_fall_through_when_newest_candidate_is_unverifiable(
    monkeypatch,
) -> None:
    security = SECURITIES["0700.HK"]
    newest = _Candidate(
        "https://www.morningstar.com/company-reports/newest",
        datetime(2026, 8, 20, tzinfo=UTC),
    )
    stale = _Candidate(
        security.curated_urls[0],
        datetime(2026, 8, 12, tzinfo=UTC),
        security.curated_values[0],
    )
    provider = MorningstarPublicProvider(reader_min_interval=0, session=_Session([]))
    monkeypatch.setattr(provider, "_discover", lambda _security: [newest, stale])
    calls: list[_Candidate] = []

    def fake_read(candidate, _security):
        calls.append(candidate)
        raise ValueError("最新研究页未暴露可验证数值")

    monkeypatch.setattr(provider, "_read", fake_read)
    values, failures = provider.fetch_all(
        {"0700.HK": security},
        checked_at=datetime(2026, 8, 29, tzinfo=UTC),
    )
    assert values == {}
    assert "0700.HK" in failures
    assert calls == [newest]


def test_live_value_is_saved_with_fixed_currency(tmp_path) -> None:
    now = datetime(2026, 8, 28, tzinfo=UTC)
    values, failures = refresh_fair_values(
        provider=_Provider({"AAPL": _value()}),
        state_dir=tmp_path,
        prices={"AAPL": 230.0},
        checked_at=now,
    )
    assert values["AAPL"].fair_value == 290.0
    assert "AAPL" not in failures
    cached = load_cache(tmp_path / "morningstar_fair_values.json")
    assert cached["AAPL"].currency == "USD"


def test_short_outage_uses_recent_verified_cache(tmp_path) -> None:
    now = datetime(2026, 8, 28, tzinfo=UTC)
    refresh_fair_values(
        provider=_Provider({"AAPL": _value()}),
        state_dir=tmp_path,
        prices={"AAPL": 230.0},
        checked_at=now,
    )
    values, _ = refresh_fair_values(
        provider=_Provider(failures={"AAPL": "timeout"}),
        state_dir=tmp_path,
        prices={"AAPL": 230.0},
        checked_at=now + timedelta(hours=24),
    )
    assert values["AAPL"].fallback_used is True
    assert values["AAPL"].fair_value_updated_at == "2026-08-07"


def test_cache_older_than_48_hours_is_not_published(tmp_path) -> None:
    now = datetime(2026, 8, 28, tzinfo=UTC)
    refresh_fair_values(
        provider=_Provider({"AAPL": _value()}),
        state_dir=tmp_path,
        prices={"AAPL": 230.0},
        checked_at=now,
    )
    values, failures = refresh_fair_values(
        provider=_Provider(failures={"AAPL": "timeout"}),
        state_dir=tmp_path,
        prices={"AAPL": 230.0},
        checked_at=now + timedelta(hours=49),
    )
    assert "AAPL" not in values
    assert failures["AAPL"] == "timeout"


def test_same_date_changed_value_is_rejected(tmp_path) -> None:
    now = datetime(2026, 8, 28, tzinfo=UTC)
    refresh_fair_values(
        provider=_Provider({"AAPL": _value()}),
        state_dir=tmp_path,
        prices={"AAPL": 230.0},
        checked_at=now,
    )
    values, failures = refresh_fair_values(
        provider=_Provider(
            {"AAPL": _value(fair_value=310.0, retrieved=(now + timedelta(hours=1)).isoformat())}
        ),
        state_dir=tmp_path,
        prices={"AAPL": 230.0},
        checked_at=now + timedelta(hours=1),
    )
    assert values["AAPL"].fair_value == 290.0
    assert values["AAPL"].fallback_used is True
    assert "相同估值日期" in failures["AAPL"]


def test_corrupt_cache_is_ignored(tmp_path) -> None:
    path = tmp_path / "morningstar_fair_values.json"
    path.write_text(json.dumps({"fair_values": [{"ticker": "AAPL"}]}), encoding="utf-8")
    assert load_cache(path) == {}
