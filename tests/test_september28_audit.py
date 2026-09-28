"""Offline audit counterexamples; never send real mail or query live markets."""

from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from src.collectors import stocks
from src.collectors.sentiment import METRIC_NAMES, SentimentBundle, SentimentMetric
from src.config import HOLDINGS
from src.processors.thesis.renderer import build_judgment_section
from src.renderer.render import render_email
from src.utils.brief_audit import content_report
from tests.processors.thesis.test_renderer import FACT, TODAY, source
from tests.test_stocks import _history


def test_invalid_evidence_row_cannot_borrow_another_rows_validation():
    obj = source(published="2020-01-01")
    forged = deepcopy(obj.evidence[0])
    forged.update(published_at=TODAY.isoformat(), original_title="无关内容")
    obj.evidence.append(forged)
    assert build_judgment_section(sources={"company_news": [obj]}, today=TODAY) is None


def test_frontier_placeholder_cannot_keep_hidden_news_judgment():
    obj = source()
    point = SimpleNamespace(text=FACT, lab="OpenAI", source_url=obj.evidence[0]["url"],
                            source_name="Reuters", evidence=obj.evidence)
    section = build_judgment_section(sources={"frontier_labs": [point]}, today=TODAY)
    assert section
    html = render_email(signals=[], generated_at=datetime(2026, 9, 26, tzinfo=UTC),
                        frontier_labs_items=[point], frontier_labs_fallback_note="本期从略。",
                        judgment_section=section)
    assert FACT not in html
    assert section.items[0]["thesis"] not in html


@pytest.mark.parametrize("metadata", [
    None, {"symbol": "AAPL", "currency": "USD", "exchangeName": "NMS"},
    {"symbol": "MSFT", "currency": "HKD", "exchangeName": "HKG"},
])
def test_weekly_primary_must_validate_identity_before_buy_signal(monkeypatch, metadata):
    monkeypatch.setattr(stocks, "_yf_verified_daily", lambda _: stocks.PriceHistory([50], observed_at="2026-09-03"))
    monkeypatch.setattr(stocks.yf, "Ticker", lambda _: SimpleNamespace(
        _price_history=SimpleNamespace(_history_metadata=metadata)))
    monkeypatch.setattr(stocks, "_yf_history", lambda _: _history([100] * 220))
    monkeypatch.setattr(stocks, "_history_closes", lambda *a, **k: [100] * 220)
    backup = []
    def weekly(_):
        backup.append(True)
        return [25] * 220, None
    monkeypatch.setattr(stocks, "_yahoo_chart_weekly", weekly)
    result = stocks.fetch_one(HOLDINGS[0])
    assert backup == [True]
    assert result.signal == "NONE"
    assert result.sma_120 == 25


def test_empty_sentiment_bundle_is_not_verified_content():
    report = content_report(signals=[], valuations={}, expected_tickers=[], news={},
                            sentiment=SentimentBundle([], datetime.now(UTC)))
    assert report["status"] == "degraded"


@pytest.mark.parametrize("health", [
    {"macro": {"source_failures": 6}},
    {"sentiment": {"processing_failures": 1}},
    {"frontier": {"fallback": True}},
])
def test_section_failure_is_archived_even_without_news_candidates(health):
    report = content_report(signals=[], valuations={}, sentiment=None, expected_tickers=[],
                            news={"macro": (0, [])}, section_health=health)
    assert report["status"] == "degraded"
    assert report["section_health"] == health


def test_genuine_no_news_is_not_a_processing_failure():
    report = content_report(signals=[], valuations={}, sentiment=None, expected_tickers=[],
                            news={"macro": (0, [])}, section_health={"macro": {
                                "source_failures": 0, "processing_failures": 0, "fallback": False}})
    assert report["status"] == "verified"


def test_timeout_keeps_all_expected_observations_in_denominator():
    report = content_report(signals=[], valuations={}, sentiment=SentimentBundle([], datetime.now(UTC)),
                            expected_tickers=[], news={}, expected_prices=[h.ticker for h in HOLDINGS],
                            expected_metrics=METRIC_NAMES)
    assert report["counts"]["missing"] == len(HOLDINGS) + len(METRIC_NAMES)
    assert len(report["observations"]) == 20


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True])
def test_nonfinite_or_boolean_sentiment_is_missing_not_verified(value):
    metric = SentimentMetric("VIX", value, 15, None, observed_at="2026-09-25")
    report = content_report(signals=[], valuations={}, sentiment=SentimentBundle([metric], datetime.now(UTC)),
                            expected_tickers=[], news={})
    assert report["counts"]["missing"] == 1
    assert report["observations"][0]["current"] is None


def test_successful_editorial_silence_is_not_a_quality_failure():
    report = content_report(signals=[], valuations={}, sentiment=None, expected_tickers=[],
                            news={"company": (12, [])}, section_health={"company": {"silence": True}})
    assert report["status"] == "verified"
    report = content_report(signals=[], valuations={}, sentiment=None, expected_tickers=[],
                            news={"company": (12, [])}, section_health={"company": {
                                "silence": True, "source_failures": 1}})
    assert report["status"] == "degraded"


@pytest.mark.parametrize("symbol,currency,exchange", [
    ("MSFT", "USD", "NMS"), ("0700.HK", "HKD", "HKG"),
])
def test_weekly_response_identity_reads_no_new_metadata_request(symbol, currency, exchange):
    class Ticker:
        _price_history = SimpleNamespace(_history_metadata={
            "symbol": symbol, "currency": currency, "exchangeName": exchange})

        @property
        def history_metadata(self):
            pytest.fail("must not fetch a new intraday response")

    stocks._validate_yf_identity(Ticker(), symbol)
