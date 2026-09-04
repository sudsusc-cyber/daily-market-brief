from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
import requests

from src.valuation.morningstar import (
    SECURITIES,
    MorningstarFairValue,
    MorningstarPublicProvider,
    _Candidate,
    _extract_value,
    _parse_company_report_candidates,
    _reconcile_independent_sources,
    load_cache,
    refresh_fair_values,
)
from src.valuation.yahoo_morningstar import _VALUE_RE, YahooMorningstarProvider


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
            headline="Tencent Earnings",
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


def test_newer_report_can_confirm_unchanged_fair_value() -> None:
    official = _value(fair_value=600.0, updated="2026-08-11")
    distributed = MorningstarFairValue(
        **{
            **official.__dict__,
            "fair_value_updated_at": "2026-08-20",
            "source_provider": "Morningstar report distributed by Yahoo Finance",
            "source_url": "https://finance.yahoo.com/research/reports/example/",
        }
    )
    result = _reconcile_independent_sources(official, distributed)
    assert result.fair_value == 600.0
    assert result.source_provider.endswith("Yahoo Finance")
    assert "维持原公允价值" in str(result.warning)


def test_newer_distributed_report_replaces_older_explicit_value() -> None:
    official = _value(fair_value=280.0, updated="2026-08-20")
    distributed = MorningstarFairValue(
        **{
            **official.__dict__,
            "fair_value": 310.0,
            "fair_value_updated_at": "2026-08-26",
            "source_provider": "Morningstar report distributed by Yahoo Finance",
            "source_url": "https://finance.yahoo.com/research/reports/example/",
        }
    )
    result = _reconcile_independent_sources(official, distributed)
    assert result.fair_value == 310.0
    assert "较旧来源为 280 USD" in str(result.warning)


def test_same_report_date_source_conflict_prefers_official_primary() -> None:
    official = _value(fair_value=280.0, updated="2026-08-20")
    distributed = MorningstarFairValue(
        **{
            **official.__dict__,
            "fair_value": 310.0,
            "source_provider": "Morningstar report distributed by Yahoo Finance",
        }
    )
    result = _reconcile_independent_sources(official, distributed)
    assert result.fair_value == 280.0
    assert result.source_provider == "Morningstar public research"
    assert "Yahoo 备源为 310 USD" in str(result.warning)


def test_yahoo_curated_report_survives_search_rate_limit(monkeypatch) -> None:
    provider = YahooMorningstarProvider(tesseract_path="tesseract")
    monkeypatch.setattr(
        provider,
        "_search",
        lambda _params: (_ for _ in ()).throw(ValueError("HTTP 429")),
    )
    report_id, published, report_url, snapshot_url = provider._latest_report(
        SECURITIES["MSFT"]
    )
    assert report_id.startswith("MS_0P000003MH_AnalystReport_")
    assert published.date().isoformat() == "2026-07-31"
    assert report_url.startswith("https://finance.yahoo.com/research/reports/")
    assert snapshot_url and snapshot_url.startswith("https://s.yimg.com/")


def test_yahoo_ocr_pattern_handles_joined_following_column() -> None:
    match = _VALUE_RE.search("600.00USD75")
    assert match is not None
    assert match.groups() == ("600.00", "USD")


def test_yahoo_does_not_substitute_adr_for_hk_listing() -> None:
    assert YahooMorningstarProvider._symbol("0700.HK") is None
    assert YahooMorningstarProvider._symbol("9992.HK") is None
    assert YahooMorningstarProvider._symbol("BRK.B") == "BRK-B"


def test_yahoo_report_is_used_when_official_page_is_unavailable(monkeypatch) -> None:
    backup = MorningstarFairValue(
        **{
            **_value().__dict__,
            "source_provider": "Morningstar report distributed by Yahoo Finance",
            "source_url": "https://finance.yahoo.com/research/reports/example/",
        }
    )
    provider = MorningstarPublicProvider(
        session=_Session([]),
        secondary_provider=_Provider({"AAPL": backup}),
    )
    monkeypatch.setattr(provider, "_discover", lambda _security: [])
    values, failures = provider.fetch_all(
        {"AAPL": SECURITIES["AAPL"]},
        checked_at=datetime(2026, 8, 29, tzinfo=UTC),
    )
    assert failures == {}
    assert values["AAPL"].fair_value == 290.0
    assert values["AAPL"].fallback_used is True
    assert values["AAPL"].source_provider.endswith("Yahoo Finance")


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


@pytest.mark.parametrize("ticker,headline,value", [
    ("0700.HK", "Tencent: HKD 780 Fair Value Unchanged", 780),
    ("0700.HK", "Tencent: HKD 825.50 Fair Value Estimate Maintained", 825.5),
    ("0700.HK", "Tencent: Fair Value Estimate Raised to HKD 910", 910),
    ("0700.HK", "Tencent: Fair Value Unchanged at HK$ 800", 800),
    ("0700.HK", "Tencent: Fair Value: HKD 1,020", 1020),
    ("9992.HK", "Pop Mart: HKD 224 Fair Value Unchanged", 224),
])
def test_hk_explicit_headline_values_are_dynamic(ticker, headline, value) -> None:
    assert _extract_value(f"Title: {headline}\nMorningstar", SECURITIES[ticker]) == (value, "HKD")


@pytest.mark.parametrize("headline", [
    "Tencent: Fair Value Cut by 20%",  # 百分比不是港币公允价值。
    "Tencent: HKD 780 Revenue Expected",  # 其他指标。
    "Tencent: USD 100 Fair Value Unchanged",  # 不得把 ADR 美元口径当港股。
    "Tencent: Prior HKD 900 Fair Value; Review Pending",  # 历史值。
    "Tencent: HKD 780 Fair Value or HKD 800 Fair Value",  # 歧义。
    "Tencent: HKD 0 Fair Value Unchanged",
    "Other Company: HKD 780 Fair Value Unchanged",
])
def test_hk_headline_does_not_guess_or_misattribute_values(headline) -> None:
    with pytest.raises(ValueError):
        _extract_value(f"Title: {headline}\nMorningstar", SECURITIES["0700.HK"])


def test_hk_does_not_extract_value_from_archived_related_reports() -> None:
    page = """
Title: Tencent: New AI Spending Update
Morningstar Tencent research. Subscribe for full report.
## Company Report Archive
### Tencent Earnings
Fair Value Estimate: HKD 900
"""
    with pytest.raises(ValueError):
        _extract_value(page, SECURITIES["0700.HK"])


_TENCENT_URL = (
    "https://www.morningstar.com/company-reports/"
    "1498254-tencent-the-ai-cash-drain-has-a-visible-end-date-hkd-780-fair-value-unchanged"
    "?listing=0P00009S22"
)
_TENCENT_DATE = datetime(2026, 9, 2, tzinfo=UTC)
_TENCENT_TITLE = "Tencent: The AI Cash Drain Has a Visible End Date; HKD 780 Fair Value Unchanged"


def _tencent_listing(title=_TENCENT_TITLE, url=_TENCENT_URL, date="Sep 2, 2026"):
    return f"### [{title}]({url})\nSummary.\nIvan Su {date}\n"


def test_live_tencent_report_regression_uses_new_date_without_known_value(monkeypatch) -> None:
    page = f"Title: {_TENCENT_TITLE}\nPublished Time: 2026-09-02T11:59:00+0000\nMorningstar"
    provider = MorningstarPublicProvider(reader_min_interval=0, session=_Session([
        _Response("Forbidden", ok=False, status_code=403), _Response(page),
        _Response("Forbidden", ok=False, status_code=403), _Response(page),
    ]))
    candidate = _Candidate(_TENCENT_URL, _TENCENT_DATE)  # 无硬编码值。
    monkeypatch.setattr(provider, "_discover", lambda _security: [candidate])
    values, failures = provider.fetch_all({"0700.HK": SECURITIES["0700.HK"]}, checked_at=datetime(2026, 9, 3, tzinfo=UTC))
    assert failures == {}
    assert values["0700.HK"].fair_value == 780
    assert values["0700.HK"].fair_value_updated_at == "2026-09-02"
    assert values["0700.HK"].observation_count == 2


def test_hk_direct_html_preserves_og_title_and_publication_date() -> None:
    html = f'''<html><head><meta property="og:title" content="{_TENCENT_TITLE}">
    <meta property="article:published_time" content="2026-09-02T11:59:00+0000"></head>
    <body>Tencent Holdings — Morningstar</body></html>'''
    provider = MorningstarPublicProvider(session=_Session([_Response(html)]))
    value = provider._read(_Candidate(_TENCENT_URL, None), SECURITIES["0700.HK"])
    assert value.fair_value == 780
    assert value.fair_value_updated_at == "2026-09-02"


def test_explicit_new_headline_value_overrides_curated_baseline() -> None:
    page = "Title: Tencent: HKD 825 Fair Value Unchanged\nPublished Time: 2026-09-02\nMorningstar"
    provider = MorningstarPublicProvider(session=_Session([_Response(page)]))
    value = provider._read(_Candidate(_TENCENT_URL, _TENCENT_DATE, known_value=780), SECURITIES["0700.HK"])
    assert value.fair_value == 825


def test_direct_timeout_still_tries_public_reader() -> None:
    page = f"Title: {_TENCENT_TITLE}\nPublished Time: 2026-09-02T11:59:00Z\nMorningstar"

    class TimeoutSession(_Session):
        def get(self, url, **kwargs):
            if not url.startswith("https://r.jina.ai/"):
                raise requests.Timeout("direct timeout")
            return super().get(url, **kwargs)

    provider = MorningstarPublicProvider(session=TimeoutSession([_Response(page)]))
    value = provider._read(_Candidate(_TENCENT_URL, _TENCENT_DATE), SECURITIES["0700.HK"])
    assert value.fair_value == 780


def test_hk_official_directory_recovers_when_both_report_paths_fail(monkeypatch) -> None:
    responses = []
    for _ in range(2):
        responses.extend([
            _Response("Forbidden", ok=False, status_code=403),
            _Response("Forbidden", ok=False, status_code=403),
            _Response(_tencent_listing()),
        ])
    provider = MorningstarPublicProvider(reader_min_interval=0, session=_Session(responses))
    monkeypatch.setattr(provider, "_discover", lambda _security: [_Candidate(_TENCENT_URL, _TENCENT_DATE)])
    values, failures = provider.fetch_all({"0700.HK": SECURITIES["0700.HK"]}, checked_at=datetime(2026, 9, 3, tzinfo=UTC))
    assert failures == {}
    value = values["0700.HK"]
    assert value.fair_value == 780 and value.fair_value_updated_at == "2026-09-02"
    assert value.source_provider == "Morningstar official report listing via Jina Reader"
    assert value.observation_count == 2


def test_directory_fallback_rejects_two_different_live_reads(monkeypatch) -> None:
    responses = []
    for amount in (780, 825):
        responses.extend([
            _Response("Forbidden", ok=False, status_code=403),
            _Response("Forbidden", ok=False, status_code=403),
            _Response(_tencent_listing(title=f"Tencent: HKD {amount} Fair Value Unchanged")),
        ])
    provider = MorningstarPublicProvider(reader_min_interval=0, session=_Session(responses))
    monkeypatch.setattr(provider, "_discover", lambda _security: [_Candidate(_TENCENT_URL, _TENCENT_DATE)])
    values, failures = provider.fetch_all({"0700.HK": SECURITIES["0700.HK"]}, checked_at=datetime(2026, 9, 3, tzinfo=UTC))
    assert not values
    assert "双读不一致" in failures["0700.HK"]


@pytest.mark.parametrize("listing", [
    _tencent_listing(url=_TENCENT_URL.replace("1498254", "1494726")),
    _tencent_listing(date="Aug 12, 2026"),
    _tencent_listing(title="Tencent: Fair Value Cut by 20%"),
    _tencent_listing(title="Other Company: HKD 780 Fair Value Unchanged"),
])
def test_directory_fallback_requires_exact_report_date_issuer_and_absolute_value(listing) -> None:
    provider = MorningstarPublicProvider(session=_Session([_Response(listing)]))
    with pytest.raises(ValueError):
        provider._read_listing(_Candidate(_TENCENT_URL, _TENCENT_DATE), SECURITIES["0700.HK"])


def test_directory_fallback_rejects_wrong_listing_identity() -> None:
    provider = MorningstarPublicProvider(session=_Session([]))
    candidate = _Candidate(_TENCENT_URL.replace("0P00009S22", "other"), _TENCENT_DATE)
    with pytest.raises(ValueError, match="上市口径"):
        provider._read_listing(candidate, SECURITIES["0700.HK"])


def test_report_listing_does_not_borrow_date_from_next_report() -> None:
    text = f"### [Undated report]({_TENCENT_URL.replace('1498254', 'other')})\nSummary\n" + _tencent_listing()
    assert [c.url for c in _parse_company_report_candidates(text)] == [_TENCENT_URL]


def test_final_check_refreshes_successes_and_failures(monkeypatch) -> None:
    provider = MorningstarPublicProvider(session=_Session([]))
    calls = []
    fail_tencent = True

    def discover(security):
        calls.append(security.ticker)
        return [_Candidate("https://www.morningstar.com/stocks/test", _TENCENT_DATE)]

    def read(candidate, security):
        if security.ticker == "0700.HK" and fail_tencent:
            raise ValueError("temporary failure")
        return _value(security.ticker, updated="2026-09-02")

    monkeypatch.setattr(provider, "_discover", discover)
    monkeypatch.setattr(provider, "_read", read)
    securities = {k: SECURITIES[k] for k in ("AAPL", "0700.HK")}
    now = datetime(2026, 9, 3, tzinfo=UTC)
    values, failures = provider.fetch_all(securities, checked_at=now)
    assert set(values) == {"AAPL"} and set(failures) == {"0700.HK"}
    fail_tencent = False
    values, failures = provider.fetch_all(securities, checked_at=now + timedelta(minutes=6))
    assert set(values) == set(securities) and failures == {}
    assert calls == ["AAPL", "0700.HK", "AAPL", "0700.HK"]
    provider.fetch_all(securities, checked_at=now + timedelta(minutes=7))
    assert len(calls) == 6
    provider.fetch_all(securities, checked_at=now + timedelta(minutes=7))
    assert len(calls) == 6  # Only the identical observation request is memoized.
