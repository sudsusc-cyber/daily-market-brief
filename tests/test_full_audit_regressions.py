"""September audit counterexamples: offline, deterministic, no SMTP/API traffic."""

import json
import signal
import smtplib
import time
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest
from bs4 import BeautifulSoup

from src.collectors import company_news, figures, sentiment, stocks
from src.config import HOLDINGS
from src.processors import figure_filter, news_summarizer
from src.processors.sentiment_judge import score_sentiment
from src.renderer.render import _build_env, render_email
from src.sender import smtp_sender
from src.utils import delivery, idempotency
from src.utils.last_good import LastGoodCache
from src.utils.market_clock import latest_closed_session, validate_history, validate_quote
from src.utils.publication import candidate_sources, published_pending, summary_urls
from src.utils.runtime_budget import RuntimeBudget, StageTimeout
from src.valuation import morningstar
from src.valuation.models import ValuationDisplay
from src.valuation.service import apply_morningstar_fair_values

NOW = datetime(2026, 9, 4, 0, tzinfo=UTC)


def holding_signal():
    return stocks.StockSignal(HOLDINGS[0], 500, 400, 300, .25, .667, "NONE")


def fair_value():
    return morningstar.MorningstarFairValue(
        "MSFT", "XNAS:MSFT", 600, "USD", "analyst", "2026-08-11", NOW.isoformat(),
        "Yahoo Morningstar", "https://finance.yahoo.com/research/reports/msft", fallback_used=True)


def test_plain_text_and_url_attributes_are_escaped_without_changing_css():
    assert _build_env().autoescape is True
    html = render_email(signals=[holding_signal()], generated_at=NOW,
        holdings_intro='<b data-audit="text">A & B</b>',
        frontier_labs_items=[SimpleNamespace(text="safe", source_name="source",
            source_url='https://example.com/" data-audit="url')])
    soup = BeautifulSoup(html, "html.parser")
    assert not soup.select("[data-audit]")
    assert '<b data-audit="text">A & B</b>' in soup.get_text()
    assert "Cambria,'Times New Roman'" in html
    assert "font-feature-settings:'lnum' 1,'tnum' 1" in html


@pytest.mark.parametrize("failure", [OSError("offline"), smtplib.SMTPServerDisconnected("offline")])
def test_partial_delivery_survives_retry_exception_and_has_immediate_receipt(monkeypatch, tmp_path, failure):
    first = MagicMock()
    first.sendmail.return_value = {"b@example.com": (450, b"try later")}
    connect = MagicMock(side_effect=[first, failure])
    monkeypatch.setattr(smtp_sender, "_connect_and_login", connect)
    monkeypatch.setattr(smtp_sender.time, "sleep", lambda _: None)
    path = tmp_path / "receipt.json"
    monkeypatch.setenv("DELIVERY_RECEIPT_PATH", str(path))
    def progress(result):
        delivery.write_delivery_receipt(sent_at=NOW, accepted_count=len(result.accepted),
                                        refused_count=len(result.refused))
    result = smtp_sender.send_html_email(sender="sender@example.com", auth_code="fixture",
        recipient=["a@example.com", "b@example.com"], subject="fixture", html_body="<p>fixture</p>",
        on_progress=progress)
    assert result.accepted == ("a@example.com",)
    assert set(result.refused) == {"b@example.com"}
    assert delivery.receipt_satisfies(path, "accepted")
    assert not delivery.receipt_satisfies(path, "full")
    assert connect.call_count == 2
    assert first.sendmail.call_count == 1


def test_watchdog_passes_through_broad_collector_error_handling():
    def stuck():
        try:
            time.sleep(.2)
        except Exception:
            pytest.fail("watchdog was swallowed by an ordinary retry handler")
    previous = signal.getsignal(signal.SIGALRM)
    result = RuntimeBudget().run("fixture", stuck, seconds=.015, fallback=lambda: "cached")
    assert result == "cached"
    assert signal.getsignal(signal.SIGALRM) == previous
    assert signal.getitimer(signal.ITIMER_REAL)[0] == 0


def test_smtp_acceptance_is_recorded_before_connection_cleanup(monkeypatch):
    server = MagicMock()
    server.sendmail.return_value = {}
    monkeypatch.setattr(smtp_sender, "_connect_and_login", lambda **_: server)
    events = []
    progress = MagicMock(side_effect=lambda _: events.append("accepted"))
    server.quit.side_effect = lambda: events.append("quit")
    result = smtp_sender.send_html_email(sender="sender@example.com", auth_code="fixture",
        recipient="a@example.com", subject="fixture", html_body="<p>fixture</p>", on_progress=progress)
    assert result.accepted == ("a@example.com",)
    assert events == ["accepted", "quit"]
    server.quit.assert_called_once()


def test_expired_budget_never_starts_a_request():
    budget = RuntimeBudget(seconds=0)
    fn = MagicMock()
    assert budget.run("fixture", fn, seconds=35, fallback=lambda: "cached") == "cached"
    fn.assert_not_called()


def test_morningstar_timeout_preserves_completed_values(monkeypatch):
    provider = morningstar.MorningstarPublicProvider(session=MagicMock(), secondary_provider=None)
    def discover(security):
        if security.ticker == "AAPL":
            raise StageTimeout("fixture")
        return [SimpleNamespace(url="https://www.morningstar.com/stocks/msft")]
    monkeypatch.setattr(provider, "_discover", discover)
    monkeypatch.setattr(provider, "_read", lambda *_: replace(fair_value(), fallback_used=False))
    values, failures = provider.fetch_all({key: morningstar.SECURITIES[key] for key in ("MSFT", "AAPL")}, checked_at=NOW)
    assert set(values) == {"MSFT"}
    assert set(failures) == {"AAPL"}


def test_morningstar_timeout_does_not_publish_unreconciled_primary(monkeypatch):
    secondary = MagicMock()
    secondary.fetch_all.return_value = ({"MSFT": fair_value()}, {})
    provider = morningstar.MorningstarPublicProvider(session=MagicMock(), secondary_provider=secondary)
    monkeypatch.setattr(provider, "_discover", lambda _: [SimpleNamespace(url="https://www.morningstar.com/msft")])
    monkeypatch.setattr(provider, "_read", lambda *_: replace(fair_value(), fallback_used=False))
    monkeypatch.setattr(morningstar, "_reconcile_independent_sources", MagicMock(side_effect=StageTimeout("fixture")))
    values, failures = provider.fetch_all({"MSFT": morningstar.SECURITIES["MSFT"]}, checked_at=NOW)
    assert values == {}
    assert set(failures) == {"MSFT"}


@pytest.mark.parametrize("index", [
    pd.RangeIndex(220),
    pd.date_range(end="2020-01-03", periods=220, freq="W-FRI"),
    pd.DatetimeIndex(["2026-08-31", "2026-08-31"]),
    pd.DatetimeIndex(["2026-08-31", "2026-08-24"]),
    pd.DatetimeIndex(["2026-09-07"]),
])
def test_history_rejects_missing_stale_duplicate_reversed_or_future_dates(index):
    with pytest.raises(ValueError):
        validate_history(index, symbol="MSFT", interval="1wk", now=NOW)


@pytest.mark.parametrize("stamp", [None, True, 0, float("nan"),
    datetime(2020, 1, 3, tzinfo=UTC).timestamp(), (NOW + timedelta(days=1)).timestamp()])
def test_quote_requires_a_recent_market_timestamp(stamp):
    with pytest.raises(ValueError):
        validate_quote(stamp, symbol="MSFT", now=NOW)


def test_exchange_holidays_and_weekly_bar_labels_are_not_confused():
    now = datetime(2026, 7, 2, 0, tzinfo=UTC)
    assert latest_closed_session("0700.HK", now) == date(2026, 6, 30)
    assert latest_closed_session("MSFT", now) == date(2026, 7, 1)
    assert validate_history(pd.DatetimeIndex(["2026-06-29"]), symbol="0700.HK", interval="1wk", now=now)
    with pytest.raises(ValueError):
        validate_history(pd.DatetimeIndex(["2026-06-30"]), symbol="MSFT", interval="1d", now=now)


def test_fresh_secondary_value_is_saved_and_survives_next_outage(tmp_path):
    provider = MagicMock()
    provider.fetch_all.return_value = ({"MSFT": fair_value()}, {})
    morningstar.refresh_fair_values(provider=provider, state_dir=tmp_path, prices={"MSFT": 500}, checked_at=NOW)
    cached = morningstar.load_cache(tmp_path / "morningstar_fair_values.json")
    assert cached["MSFT"].fair_value == 600
    assert not cached["MSFT"].stale_cache
    provider.fetch_all.return_value = ({}, {})
    values, _ = morningstar.refresh_fair_values(provider=provider, state_dir=tmp_path,
                                               prices={"MSFT": 500}, checked_at=NOW + timedelta(days=1))
    assert values["MSFT"].stale_cache
    assert values["MSFT"].retrieved_at == NOW.isoformat()


def test_one_broken_cache_row_does_not_discard_other_holdings(tmp_path):
    path = tmp_path / "morningstar.json"
    morningstar._save_cache(path, {"MSFT": fair_value()})
    payload = json.loads(path.read_text())
    payload["fair_values"].insert(0, {"ticker": "AAPL", "fair_value": "invalid"})
    path.write_text(json.dumps(payload))
    assert set(morningstar.load_cache(path)) == {"MSFT"}


def test_future_dated_snapshot_is_not_accepted_as_fresh_fallback(tmp_path):
    path = tmp_path / "morningstar_fair_values.json"
    morningstar._save_cache(path, {"MSFT": replace(fair_value(), retrieved_at=(NOW + timedelta(days=1)).isoformat())})
    provider = MagicMock()
    provider.fetch_all.return_value = ({}, {})
    values, _ = morningstar.refresh_fair_values(provider=provider, state_dir=tmp_path, prices={"MSFT": 500}, checked_at=NOW)
    assert "MSFT" not in values


def test_cached_value_is_visible_with_its_real_date_not_only_attempt_time():
    initial = {"MSFT": ValuationDisplay(ticker="MSFT", status="current")}
    value = replace(fair_value(), stale_cache=True, retrieved_at="2026-09-02T00:00:00+00:00")
    displays = apply_morningstar_fair_values(initial, fair_values={"MSFT": value}, failures={}, prices={"MSFT": 500})
    assert displays["MSFT"].status == "not_due"
    html = render_email(signals=[holding_signal()], generated_at=NOW, valuations=displays, valuation_checked_at=NOW)
    assert "600.00" in html and "2026-09-02" in html and "沿用" in html
    fresh = apply_morningstar_fair_values(initial, fair_values={"MSFT": fair_value()}, failures={}, prices={"MSFT": 500})
    assert fresh["MSFT"].status == "current" and fresh["MSFT"].data_note is None


def test_only_published_sources_consume_candidate_hashes_even_after_translation():
    item = company_news.NewsItem("Microsoft release", NOW, "https://example.com/a", "source")
    bundle = company_news.CompanyNewsBundle(HOLDINGS[0], [item])
    candidates = candidate_sources([bundle], lambda b: b.holding.ticker, company_news._content_hash)
    key = next(iter(candidates))
    pending = {"old": "old-date", key: NOW.isoformat()}
    item.title = "翻译后的标题"
    assert published_pending(pending, candidates, set()) == {"old": "old-date"}
    summary = SimpleNamespace(summary_html='<div>Actual story<a href="https://example.com/a">[1]</a></div>')
    assert published_pending(pending, candidates, summary_urls(summary)) == pending
    assert summary_urls(None) == set()


def test_recovered_official_figure_is_processed_despite_prior_source_error():
    item = figures.FigureMention('Buffett said "patience"', "", NOW, "https://example.com/official", "Official")
    bundle = figures.FigureBundle("巴菲特", "Buffett", items=[item], error="primary failed before recovery")
    client = MagicMock()
    client.chat.return_value = SimpleNamespace(text="▦ 1: no | 普通常识", error=None)
    figure_filter.filter_one(bundle, client=client)
    assert client.chat.called


def test_company_source_mismatch_is_rejected_but_real_same_company_source_passes():
    item = company_news.NewsItem("Microsoft expands Azure", NOW, "https://example.com/msft", "source")
    _, sources = news_summarizer._format_input([company_news.CompanyNewsBundle(HOLDINGS[0], [item])])
    wrong = news_summarizer._rebuild_safe_summary("<strong>苹果</strong> —— 微软扩建数据中心[1]", sources)
    right = news_summarizer._rebuild_safe_summary("<strong>微软</strong> —— 微软扩建数据中心[1]", sources)
    assert wrong is None
    assert right is not None and len(right.footnotes) == 1


def test_explicit_cross_company_source_and_company_alias_remain_valid():
    article = company_news.NewsItem("Microsoft and Apple partnership", NOW, "https://example.com/partnership", "source")
    holdings = {holding.ticker: holding for holding in HOLDINGS}
    bundles = [company_news.CompanyNewsBundle(holdings[ticker], [replace(article)]) for ticker in ("MSFT", "AAPL")]
    _, sources = news_summarizer._format_input(bundles)
    summary = news_summarizer._rebuild_safe_summary("<strong>苹果公司</strong> —— 苹果与微软合作[1]", sources)
    assert summary is not None and len(summary.footnotes) == 1


@pytest.mark.parametrize("conclusion,confirmed,expected", [
    ("success", False, False), ("success", True, True),
    ("failure", True, True), ("cancelled", True, True), ("timed_out", True, True),
])
def test_run_success_is_not_a_delivery_receipt(monkeypatch, conclusion, confirmed, expected):
    for key, value in {"GH_TOKEN": "fixture", "GH_REPO": "owner/repo", "GH_RUN_ID": "200"}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(idempotency, "_today_beijing_iso", lambda: "2026-09-04")
    response = MagicMock()
    response.__enter__.return_value = response
    response.read.return_value = json.dumps({"workflow_runs": [{"id": 100, "head_branch": "main",
        "created_at": "2026-09-04T00:00:00Z", "status": "completed", "conclusion": conclusion}]}).encode()
    monkeypatch.setattr(idempotency.urllib.request, "urlopen", lambda *a, **k: response)
    check = MagicMock(return_value=confirmed)
    monkeypatch.setattr(idempotency, "_run_has_successful_step", check)
    assert idempotency.already_sent_today() is expected
    check.assert_called_once()


@pytest.mark.parametrize("broken,expected", [("_fetch_cnn_fear_greed", "CNN Fear & Greed"),
    ("_fetch_shiller_pe", "Shiller PE"), ("_fetch_fred_hy_spread", "高收益债利差")])
def test_exception_cache_recovery_retains_scoring_identity(monkeypatch, tmp_path, broken, expected):
    def metric(name, value):
        return sentiment.SentimentMetric(name, value, value, None)
    monkeypatch.setattr(sentiment, "_fetch_cnn_fear_greed", lambda: metric("CNN Fear & Greed", 50))
    monkeypatch.setattr(sentiment, "_fetch_vix_primary", lambda: metric("VIX", 20))
    monkeypatch.setattr(sentiment, "_fetch_simple_index", lambda *a: metric("DXY", 100))
    monkeypatch.setattr(sentiment, "_fetch_shiller_pe", lambda: metric("Shiller PE", 30))
    monkeypatch.setattr(sentiment, "_fetch_fred_hy_spread", lambda *a: metric("高收益债利差", 3))
    monkeypatch.setattr(sentiment, broken, MagicMock(side_effect=RuntimeError("fixture")))
    monkeypatch.setattr(LastGoodCache, "get", lambda *a: ({"current": 50, "prior": 49}, "2026-09-03"))
    monkeypatch.setattr(LastGoodCache, "put", lambda *a, **k: None)
    bundle = sentiment.fetch_all("fixture", state_dir=tmp_path, today=NOW.date())
    score = score_sentiment(bundle)
    assert expected in [name for name, _, _ in score["breakdown"]]
