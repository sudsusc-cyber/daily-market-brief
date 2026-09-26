"""Latest-source policy: every configured holding keeps dated, genuine evidence."""

import copy
import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest
from bs4 import BeautifulSoup

from scripts.qqqm_cache import normalize_cache
from src.collectors.stocks import StockSignal
from src.config import HOLDINGS
from src.renderer.render import render_email
from src.sender.smtp_sender import send_html_email
from src.utils.brief_audit import content_report
from src.utils.runtime_budget import RuntimeBudget
from src.valuation import qqqm, qqqm_sources
from src.valuation.morningstar import SECURITIES, load_cache, refresh_fair_values
from src.valuation.service import cached_morningstar_displays, prepare_valuation_displays

CONFIG = Path(__file__).parents[1] / "config"
NOW = datetime(2026, 9, 27, tzinfo=UTC)


@pytest.mark.parametrize("mode", ["outage", "budget_exhausted", "missing_quotes"])
def test_all_15_values_survive_outage_and_render_in_one_mock_email(tmp_path, monkeypatch, mode):
    monkeypatch.setattr(qqqm, "fetch_source_packet", lambda **_: None)
    monkeypatch.setattr("src.valuation.pop_mart.PopMartTargetProvider.fetch",
                        Mock(side_effect=TimeoutError("offline")))
    signals = [StockSignal(h, 300, 310, 280, -.03, .07, "NONE", observed_at="2026-09-25")
               for h in HOLDINGS]
    if mode == "missing_quotes":
        signals = [replace(s, last_close=None, error="offline") for s in signals]
    prices = {s.holding.ticker: s.last_close for s in signals}
    q = qqqm.cached_qqqm_display(price=300, state_dir=tmp_path, checked_at=NOW)
    provider = Mock()
    provider.fetch_all.side_effect = TimeoutError("offline")
    if mode == "budget_exhausted":
        def recovery():
            return cached_morningstar_displays(state_dir=tmp_path, config_dir=CONFIG,
                                               prices=prices, checked_at=NOW), {}
        displays, _ = RuntimeBudget(seconds=0).call(
            prepare_valuation_displays, seconds=30, fallback=recovery)
        displays["QQQM"] = q
    else:
        displays, _ = prepare_valuation_displays(signals=signals, state_dir=tmp_path,
            config_dir=CONFIG, checked_at=NOW, morningstar_provider=provider, qqqm_display=q)
    assert set(displays) == {h.ticker for h in HOLDINGS}
    assert len(displays) == 15
    assert all(not v.is_pending and v.financial_as_of and v.source_url for v in displays.values())
    html = render_email(signals=signals, generated_at=NOW, valuations=displays)
    soup = BeautifulSoup(html, "html.parser")
    for holding in HOLDINGS:
        row = soup.select_one(f'tr[data-holding="{holding.ticker}"]')
        assert row.select_one('.holding-valuation-main').get_text(strip=True)
    assert "2026-09-25" in html and "2026-09-18" in html
    report = content_report(signals=[], valuations=displays, sentiment=None,
                            news={}, expected_tickers=[h.ticker for h in HOLDINGS])
    assert report["counts"] == {"current_verified": 0, "carried": 15, "missing": 0, "conflict": 0}
    smtp = MagicMock()
    smtp.__enter__.return_value = smtp
    smtp.sendmail.return_value = {}
    transport = Mock(return_value=smtp)
    monkeypatch.setattr("src.sender.smtp_sender.smtplib.SMTP_SSL", transport)
    receipt = send_html_email(sender="sender@example.com", auth_code="fixture",
        recipient="reader@example.com", subject="Offline regression", html_body=html)
    assert receipt.accepted == ("reader@example.com",)
    assert smtp.sendmail.call_count == 1


def test_latest_snapshot_survives_age_restore_and_keeps_hashes_and_dates(tmp_path):
    source = CONFIG / "qqqm_verified_snapshot_latest.json"
    original = json.loads(source.read_text())
    later = NOW + timedelta(days=180)
    assert normalize_cache(tmp_path, checked_at=later, allow_daily_forward=True)
    assert json.loads((tmp_path / "qqqm_valuation.json").read_text()) == original
    value = qqqm.cached_qqqm_display(price=350, state_dir=tmp_path, checked_at=later)
    assert value.intrinsic_value == pytest.approx(321.6102495805745)
    assert value.financial_as_of == "2026-09-25" and value.status == "not_due"
    assert value.implied_return == pytest.approx(value.intrinsic_value / 350 - 1)
    assert "2026-09-18" in value.data_note
    assert value.verified_at is None


@pytest.mark.parametrize("ticker", [t for t in SECURITIES if t != "9992.HK"])
def test_each_stock_prefers_newer_report_over_older_official_and_cached_value(tmp_path, ticker):
    base = load_cache(CONFIG / "morningstar_verified_snapshot.json")[ticker]
    newer = replace(base, fair_value=base.fair_value * 1.02,
        fair_value_updated_at="2026-09-25", report_published_at="2026-09-25T00:00:00Z",
        retrieved_at=NOW.isoformat(), source_url="https://finance.yahoo.com/research/reports/new/")
    provider = Mock()
    provider.fetch_all.return_value = ({ticker: newer}, {})
    values, _ = refresh_fair_values(provider=provider, state_dir=tmp_path, prices={},
        checked_at=NOW, baseline_path=CONFIG / "morningstar_verified_snapshot.json")
    assert values[ticker].fair_value == newer.fair_value
    provider.fetch_all.return_value = ({ticker: replace(base, retrieved_at=NOW.isoformat())}, {})
    values, _ = refresh_fair_values(provider=provider, state_dir=tmp_path, prices={},
        checked_at=NOW, baseline_path=CONFIG / "morningstar_verified_snapshot.json")
    assert values[ticker].fair_value == newer.fair_value
    assert values[ticker].fair_value_updated_at == "2026-09-25"


@pytest.mark.parametrize("newer_source", ["official", "backup"])
def test_dividend_conflict_selects_newest_actual_ex_date_not_transport_order(monkeypatch, newer_source):
    from tests.test_qqqm_sources import _history, _sources
    nav, older = _sources()
    newer = copy.deepcopy(older)
    newer["distributions"].append({"exDate": "2026-09-21", "distributionAmountPerUnit": .35})
    primary, backup = (newer, older) if newer_source == "official" else (older, newer)
    nav["effectiveDate"] = "2026-09-21"
    history = _history()
    history["lineChartData"][0]["data"][0].update(date="09/21/2026", value=nav["nav"])
    monkeypatch.setattr(qqqm_sources, "_fetch", lambda url: {
        qqqm_sources.NAV_URL: nav, qqqm_sources.DIV_URL: primary,
        qqqm_sources.NAV_HISTORY_URL: history}.get(url))
    monkeypatch.setattr(qqqm_sources, "fetch_gurufocus_pe", lambda **_: None)
    monkeypatch.setattr(qqqm_sources, "fetch_dividend_backup", lambda **_: backup)
    result = qqqm_sources.fetch_source_packet(checked_at=datetime(2026, 9, 22, tzinfo=UTC))
    assert result["div_ttm"] == pytest.approx(sum(qqqm_sources.dividend_rows(newer, anchor=date(2026, 9, 21)).values()))
    expected = qqqm_sources.DIV_URL if newer_source == "official" else qqqm_sources.DIV_BACKUP_URL
    assert result["citations"][1]["source"] == expected
    assert "2026-09-21" in result["selection_note"]
