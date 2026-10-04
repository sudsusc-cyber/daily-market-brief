import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

import pytest
from bs4 import BeautifulSoup

from src.collectors.stocks import StockSignal
from src.config import HOLDINGS
from src.renderer.render import render_email
from src.utils.brief_audit import content_report
from src.valuation.models import ValuationDisplay
from src.valuation.morningstar import SECURITIES, _save_cache, load_cache
from src.valuation.service import cached_morningstar_displays, prepare_valuation_displays
from src.valuation.user_reference import apply_user_references, load_user_references

CONFIG = Path(__file__).parents[1] / "config"
NOW = datetime(2026, 10, 7, tzinfo=UTC)
BASE = load_cache(CONFIG / "morningstar_verified_snapshot.json")["MCO"]
HOLDING = next(h for h in HOLDINGS if h.ticker == "MCO")


def _config(tmp_path):
    folder = tmp_path / "config"
    folder.mkdir()
    (folder / "user_fair_value_references.json").write_text(
        (CONFIG / "user_fair_value_references.json").read_text())
    return folder


def _apply(config, value=None, price=400, checked_at=NOW):
    return apply_user_references({"MCO": ValuationDisplay("MCO", "source_unavailable")},
        fair_values={"MCO": value} if value else {}, prices={"MCO": price},
        config_dir=config, checked_at=checked_at)["MCO"]


@pytest.mark.parametrize("mode", ["outage", "older_live", "watchdog_recovery"])
def test_user_reference_reaches_real_render_paths_without_rewriting_research_cache(tmp_path, mode):
    config = _config(tmp_path)
    state = tmp_path / "state"
    _save_cache(state / "morningstar_fair_values.json", {"MCO": BASE})
    signal = StockSignal(HOLDING, 400, 390, 380, .03, .05, "NONE")
    if mode == "watchdog_recovery":
        displays = cached_morningstar_displays(state_dir=state, config_dir=config,
            prices={"MCO": 400}, checked_at=NOW)
    else:
        provider = Mock()
        if mode == "outage":
            provider.fetch_all.side_effect = TimeoutError("offline")
        else:
            provider.fetch_all.return_value = ({"MCO": BASE}, {})
        displays, _ = prepare_valuation_displays(signals=[signal], state_dir=state,
            config_dir=config, checked_at=NOW, morningstar_provider=provider)
    value = displays["MCO"]
    assert value.intrinsic_value == 540
    assert value.implied_return == pytest.approx(.35)
    assert value.reference_origin == "user_provided"
    assert value.approved_at == "2026-10-04"
    assert value.financial_as_of is None and value.verified_at is None and value.source_url is None
    assert load_cache(state / "morningstar_fair_values.json")["MCO"].fair_value == 520
    html = render_email(signals=[signal], valuations=displays, generated_at=NOW)
    soup = BeautifulSoup(html, "html.parser")
    row = soup.select_one('tr[data-holding="MCO"]')
    assert "540.00" in row.select_one(".holding-valuation-main").get_text()
    assert "除用户指定参考外" in html
    assert "MCO 采用用户指定参考 540 USD（2026-10-04）" in html
    report = content_report(signals=[], valuations=displays, sentiment=None, news={}, expected_tickers=["MCO"])
    assert report["counts"]["current_verified"] == 0
    assert report["observations"][0] == {
        "section": "valuation", "key": "MCO", "status": "carried", "observed_at": None,
        "verified_at": None, "source": "user_provided", "source_type": "user_provided",
        "provided_on": "2026-10-04", "reference_value": 540,
    }


@pytest.mark.parametrize("amount", [550, 500])
def test_newer_verified_value_replaces_reference_and_survives_outage_and_old_report(tmp_path, amount):
    config = _config(tmp_path)
    state = tmp_path / "state"
    signal = StockSignal(HOLDING, 400, 390, 380, .03, .05, "NONE")
    newer = replace(BASE, fair_value=amount, fair_value_updated_at="2026-10-06",
        report_published_at="2026-10-06", valuation_as_of="2026-10-05", retrieved_at=NOW.isoformat())
    provider = Mock()
    provider.fetch_all.return_value = ({"MCO": newer}, {})
    displays, _ = prepare_valuation_displays(signals=[signal], state_dir=state,
        config_dir=config, checked_at=NOW, morningstar_provider=provider)
    assert displays["MCO"].intrinsic_value == amount
    assert displays["MCO"].reference_origin is None
    provider.fetch_all.return_value = ({"MCO": BASE}, {})
    displays, _ = prepare_valuation_displays(signals=[signal], state_dir=state,
        config_dir=config, checked_at=NOW, morningstar_provider=provider)
    assert displays["MCO"].intrinsic_value == amount
    recovered = cached_morningstar_displays(state_dir=state, config_dir=config,
        prices={"MCO": 400}, checked_at=NOW)
    assert recovered["MCO"].intrinsic_value == amount
    assert recovered["MCO"].reference_origin is None


@pytest.mark.parametrize("data_date", ["2026-10-03", "2026-10-04"])
def test_late_publication_or_same_date_cannot_retire_user_reference(tmp_path, data_date):
    config = _config(tmp_path)
    value = replace(BASE, fair_value=550, fair_value_updated_at="2026-10-06",
        report_published_at="2026-10-06", valuation_as_of=data_date, retrieved_at=NOW.isoformat())
    assert _apply(config, value).intrinsic_value == 540


@pytest.mark.parametrize("changes", [
    {"observation_count": 1}, {"currency": "HKD"}, {"provider_code": "XNAS:MCO"},
    {"source_url": "https://example.com/estimate"}, {"historical_only": True},
    {"extraction_verified": False},
])
def test_unqualified_newer_candidate_cannot_retire_user_reference(tmp_path, changes):
    config = _config(tmp_path)
    value = replace(BASE, fair_value=550, fair_value_updated_at="2026-10-06",
        report_published_at="2026-10-06", valuation_as_of="2026-10-05",
        retrieved_at=NOW.isoformat(), **changes)
    assert _apply(config, value).intrinsic_value == 540


@pytest.mark.parametrize("price", [None, 0, float("nan"), float("inf"), True])
def test_missing_or_invalid_price_keeps_reference_without_inventing_irr(tmp_path, price):
    value = _apply(_config(tmp_path), price=price)
    assert value.intrinsic_value == 540 and value.implied_return is None


def test_provided_date_uses_beijing_calendar_without_claiming_observation_date(tmp_path):
    config = _config(tmp_path)
    assert load_user_references(config, checked_at=datetime(2026, 10, 3, 15, 59, tzinfo=UTC)) == []
    assert _apply(config, checked_at=datetime(2026, 10, 3, 16, tzinfo=UTC)).intrinsic_value == 540


@pytest.mark.parametrize("changes", [
    {"fair_value": True}, {"fair_value": -1}, {"fair_value": float("nan")},
    {"currency": "HKD"}, {"ticker": "QQQM"}, {"provided_on": "tomorrow"},
    {"replace_when": "always"},
])
def test_invalid_user_config_does_not_override_display(tmp_path, changes):
    config = _config(tmp_path)
    path = config / "user_fair_value_references.json"
    payload = json.loads(path.read_text())
    payload["references"][0].update(changes)
    path.write_text(json.dumps(payload))
    assert _apply(config).intrinsic_value is None


@pytest.mark.parametrize("ticker", ["AAPL", "0700.HK"])
def test_reference_policy_is_driven_by_config_listing_and_currency(tmp_path, ticker):
    config = _config(tmp_path)
    path = config / "user_fair_value_references.json"
    security = SECURITIES[ticker]
    payload = json.loads(path.read_text())
    payload["references"][0].update(ticker=ticker, provider_code=security.provider_code,
        currency=security.currency, fair_value=600)
    path.write_text(json.dumps(payload))
    displays = {t: ValuationDisplay(t, "source_unavailable") for t in (ticker, "MCO")}
    result = apply_user_references(displays, fair_values={}, prices={ticker: 500},
        config_dir=config, checked_at=NOW)
    assert result[ticker].intrinsic_value == 600
    assert result[ticker].implied_return == pytest.approx(.2)
    assert result[ticker].currency_symbol == ("$" if security.currency == "USD" else "HK$")
    assert result["MCO"].intrinsic_value is None
