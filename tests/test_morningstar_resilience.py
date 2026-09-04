"""Shared fault-injection contract for every holding (no network/model calls)."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest

from scripts.morningstar_cache import normalize
from src.valuation.models import ValuationDisplay
from src.valuation.morningstar import (
    SECURITIES,
    _choose_verified,
    _reconcile_independent_sources,
    _save_cache,
    load_cache,
    load_verified_values,
    refresh_fair_values,
)
from src.valuation.service import apply_morningstar_fair_values, cached_morningstar_displays

CONFIG = Path(__file__).resolve().parents[1] / "config"
BASELINE = CONFIG / "morningstar_verified_snapshot.json"
NOW = datetime(2026, 9, 5, tzinfo=UTC)


@pytest.mark.parametrize("ticker", SECURITIES)
def test_every_holding_survives_total_outage_with_dated_history(tmp_path, ticker):
    provider = Mock()
    provider.fetch_all.side_effect = TimeoutError("all providers unavailable")
    values, _ = refresh_fair_values(provider=provider, state_dir=tmp_path,
        prices={}, checked_at=NOW + timedelta(days=60), baseline_path=BASELINE)
    value = values[ticker]
    baseline = load_cache(BASELINE)[ticker]
    assert value.fair_value == baseline.fair_value
    assert value.retrieved_at == baseline.retrieved_at
    assert value.stale_cache
    display = apply_morningstar_fair_values(
        {ticker: ValuationDisplay(ticker=ticker, status="not_due")},
        fair_values=values, failures={}, prices={ticker: value.fair_value},
    )[ticker]
    if ticker == "9992.HK":
        assert value.historical_only and display.verified_at is None
        assert display.is_pending and not display.is_attractive
        assert display.intrinsic_value is None and display.implied_return is None
        assert "未通过原文核验，已停用" in display.data_note
    else:
        assert not display.is_pending and display.data_note
        assert display.verified_at == baseline.retrieved_at


def test_expired_outer_budget_can_restore_all_holdings_without_network(tmp_path):
    displays = cached_morningstar_displays(state_dir=tmp_path, config_dir=CONFIG,
                                          prices={}, checked_at=NOW)
    assert set(displays) == set(SECURITIES)
    assert all(not value.is_pending and value.data_note
               for ticker, value in displays.items() if ticker != "9992.HK")
    assert displays["9992.HK"].intrinsic_value == 203.0
    assert displays["9992.HK"].formula_id == "analyst_target_price_gap"


def test_same_day_timestamp_newest_wins_not_retrieval_time():
    base = load_cache(BASELINE)["TSM"]
    earlier = replace(base, fair_value=500, report_published_at="2026-08-05T10:00:00Z")
    later = replace(base, fair_value=534, report_published_at="2026-08-05T18:00:00Z",
                    source_url="https://finance.yahoo.com/research/reports/tsm/")
    assert _reconcile_independent_sources(earlier, later).fair_value == 534
    assert _choose_verified(later, earlier).fair_value == 534


def test_date_only_does_not_invent_midnight_ordering():
    base = load_cache(BASELINE)["TSM"]
    unknown_time = replace(base, report_published_at="2026-08-05", fair_value=500)
    exact_time = replace(base, report_published_at="2026-08-05T18:00:00Z")
    selected = _choose_verified(exact_time, unknown_time)
    assert selected.fair_value == 500 and selected.stale_cache
    assert "分歧" in selected.warning


def test_disagreeing_source_is_not_counted_as_confirmation():
    base = replace(load_cache(BASELINE)["TSM"], observation_count=2)
    other = replace(base, fair_value=500, source_url="https://finance.yahoo.com/research/reports/tsm/")
    selected = _reconcile_independent_sources(base, other)
    assert selected.observation_count == 2
    assert {row["fair_value"] for row in selected.evidence} == {500, 534}


def test_wrong_ticker_returned_under_correct_key_cannot_poison_history(tmp_path):
    provider = Mock()
    provider.fetch_all.return_value = ({"TSM": load_cache(BASELINE)["AAPL"]}, {})
    values, failures = refresh_fair_values(provider=provider, state_dir=tmp_path,
        prices={}, checked_at=NOW, baseline_path=BASELINE)
    assert values["TSM"].fair_value == 534 and "标的代码" in failures["TSM"]


def test_invalid_and_future_cache_rows_are_isolated(tmp_path):
    values = load_cache(BASELINE)
    values["TSM"] = replace(values["TSM"], retrieved_at=(NOW + timedelta(days=5)).isoformat())
    _save_cache(tmp_path / "morningstar_fair_values.json", values)
    loaded = load_verified_values(state_dir=tmp_path, checked_at=NOW, prices={})
    assert "TSM" not in loaded and len(loaded) == len(SECURITIES) - 1


def test_cache_recovery_preserves_newer_daily_report_against_old_independent_copy(tmp_path):
    rows = load_cache(BASELINE)
    newer = replace(rows["TSM"], fair_value=550, fair_value_updated_at="2026-09-04",
                    report_published_at="2026-09-04T06:00:00Z", retrieved_at=NOW.isoformat())
    _save_cache(tmp_path / "morningstar_fair_values.json", {"TSM": newer})
    normalize(tmp_path, CONFIG, checked_at=NOW)
    _save_cache(tmp_path / "morningstar_fair_values.json", rows)
    normalize(tmp_path, CONFIG, checked_at=NOW)
    assert load_cache(tmp_path / "morningstar_fair_values.json")["TSM"].fair_value == 550


def test_diagnostic_includes_candidates_previous_selection_and_conflict(tmp_path):
    base = load_cache(BASELINE)["TSM"]
    provider = Mock()
    provider.fetch_all.return_value = ({"TSM": replace(base, fair_value=500)}, {})
    refresh_fair_values(provider=provider, state_dir=tmp_path, prices={},
                        checked_at=NOW, baseline_path=BASELINE)
    audit = json.loads((tmp_path / "morningstar_diagnostic.json").read_text())["securities"]["TSM"]
    assert audit["candidate"]["fair_value"] == 500
    assert audit["previous"]["fair_value"] == audit["selected"]["fair_value"] == 534
    assert audit["reason"]


def test_legacy_pop_mart_cache_is_downgraded_not_reverified(tmp_path):
    raw = json.loads(BASELINE.read_text())
    row = next(row for row in raw["fair_values"] if row["ticker"] == "9992.HK")
    row.pop("historical_only")
    row["stale_cache"] = False
    row["observation_count"] = 2
    (tmp_path / "legacy.json").write_text(json.dumps({"fair_values": [row]}))
    loaded = load_cache(tmp_path / "legacy.json")["9992.HK"]
    assert loaded.historical_only and loaded.stale_cache


def test_real_absolute_value_can_replace_same_date_unverified_legacy_constant():
    old = load_cache(BASELINE)["9992.HK"]
    real = replace(old, fair_value=210, historical_only=False, stale_cache=False,
                   observation_count=2, extraction_verified=True)
    selected = _choose_verified(real, old)
    assert selected.fair_value == 210 and not selected.historical_only


def test_rejected_legacy_reference_never_leaks_its_value_or_irr():
    old = load_cache(BASELINE)["9992.HK"]
    display = apply_morningstar_fair_values(
        {"9992.HK": ValuationDisplay(ticker="9992.HK", status="current",
            intrinsic_value=224, implied_return=0.4, verified_at=old.retrieved_at,
            source_url=old.source_url, historical_reference=True)},
        fair_values={"9992.HK": old}, failures={}, prices={"9992.HK": 150},
    )["9992.HK"]
    assert display.intrinsic_value is display.implied_return is display.verified_at is None
    assert display.source_url is None and not display.historical_reference
    assert "已停用" in display.data_note


def test_verified_pop_mart_value_is_still_numeric_with_price_gap_return():
    # Synthetic evidence tests the acceptance contract, not a live estimate.
    verified = replace(load_cache(BASELINE)["9992.HK"], fair_value=210,
        historical_only=False, stale_cache=False, observation_count=2,
        extraction_verified=True, warning=None)
    display = apply_morningstar_fair_values(
        {"9992.HK": ValuationDisplay(ticker="9992.HK", status="not_due")},
        fair_values={"9992.HK": verified}, failures={}, prices={"9992.HK": 150},
    )["9992.HK"]
    assert display.intrinsic_value == 210
    assert display.implied_return == pytest.approx(0.4)
    assert display.is_attractive and not display.is_pending
    assert display.data_note is None


@pytest.mark.parametrize("reverse", [False, True])
def test_real_history_beats_newer_unverified_reference_in_cache(tmp_path, reverse):
    legacy = load_cache(BASELINE)["9992.HK"]
    verified = replace(legacy, fair_value=210, historical_only=False,
        observation_count=2, extraction_verified=True, fair_value_updated_at="2026-08-01",
        report_published_at="2026-08-01", source_url="https://www.morningstar.com/company-reports/verified")
    from dataclasses import asdict

    rows = [asdict(verified), asdict(legacy)]
    if reverse:
        rows.reverse()
    (tmp_path / "mixed.json").write_text(json.dumps({"fair_values": rows}))
    assert load_cache(tmp_path / "mixed.json")["9992.HK"].fair_value == 210
    _save_cache(tmp_path / "morningstar_fair_values.json", {"9992.HK": verified})
    assert load_verified_values(state_dir=tmp_path, checked_at=NOW, prices={},
                                baseline_path=BASELINE)["9992.HK"].fair_value == 210
    chosen = _choose_verified(verified, legacy)
    assert chosen.fair_value == 210 and chosen.stale_cache and not chosen.historical_only
    assert "较新报告尚未核实" in chosen.warning


def test_check_script_does_not_count_legacy_as_verified(tmp_path, monkeypatch, capsys):
    from scripts import check_morningstar

    baseline = load_cache(BASELINE)
    values = {"TSM": replace(baseline["TSM"], stale_cache=True),
              "9992.HK": baseline["9992.HK"]}
    monkeypatch.setattr("sys.argv", ["check_morningstar", "--state-dir", str(tmp_path)])
    monkeypatch.setattr(check_morningstar, "MorningstarPublicProvider", lambda **_: object())
    monkeypatch.setattr(check_morningstar, "refresh_fair_values", lambda **_: (values, {}))
    check_morningstar.main()
    output = capsys.readouterr().out
    assert "Verified=1; live=0; carried=1; excluded=1" in output
    assert "9992.HK: unverified legacy record excluded" in output
    assert "224" not in output


@pytest.mark.parametrize("legacy_present", [False, True])
def test_newly_verified_historical_report_survives_next_outage(tmp_path, legacy_present):
    legacy = load_cache(BASELINE)["9992.HK"]
    if legacy_present:
        _save_cache(tmp_path / "morningstar_fair_values.json", {"9992.HK": legacy})
    # Synthetic source evidence, not a claimed current estimate.
    verified = replace(legacy, fair_value=210, historical_only=False,
        observation_count=2, extraction_verified=True, stale_cache=True, fallback_used=True,
        fair_value_updated_at="2026-08-01", report_published_at="2026-08-01",
        source_url="https://www.morningstar.com/company-reports/verified",
        warning="较新报告尚未核实，保留已读取历史报告值")
    provider = Mock()
    provider.fetch_all.return_value = ({"9992.HK": verified}, {})
    refresh_fair_values(provider=provider, state_dir=tmp_path, prices={}, checked_at=NOW)
    for name in ("morningstar_fair_values.json", "morningstar_last_verified.json"):
        saved = load_cache(tmp_path / name)["9992.HK"]
        assert saved.fair_value == 210 and not saved.historical_only
        assert saved.retrieved_at == verified.retrieved_at
    provider.fetch_all.return_value = ({}, {"9992.HK": "network unavailable"})
    values, _ = refresh_fair_values(provider=provider, state_dir=tmp_path, prices={},
                                    checked_at=NOW + timedelta(days=1))
    assert values["9992.HK"].fair_value == 210 and not values["9992.HK"].historical_only
