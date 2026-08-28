from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.valuation.engine import (
    ValuationSnapshot,
    calculate_display,
    present_value_with_terminal,
    solve_holding_period_irr,
    solve_implied_rate,
)
from src.valuation.models import FreshnessResult, OfficialDocument
from src.valuation.policy import POLICIES


def _fresh(ticker: str, document_id: str = "doc") -> FreshnessResult:
    now = datetime(2026, 8, 28, tzinfo=UTC)
    document = OfficialDocument(
        document_id=document_id,
        document_type="10-Q",
        report_period="2026-06-30",
        published_at=now,
        discovered_at=now,
        source_url="https://www.sec.gov/example",
        source_domain="sec.gov",
        content_hash="hash",
    )
    return FreshnessResult(
        ticker=ticker,
        status="current",
        checked_at=now,
        latest_document=document,
        valuation_document_id=document_id,
    )


def test_level_perpetuity_and_implied_rate() -> None:
    flows = (10.0,) * 5
    value = present_value_with_terminal(flows, rate=0.10, terminal_growth=0.0)
    assert value == pytest.approx(100.0)
    implied = solve_implied_rate(
        target_value=100.0,
        value_at_rate=lambda rate: present_value_with_terminal(
            flows, rate=rate, terminal_growth=0.0
        ),
        terminal_growth=0.0,
    )
    assert implied == pytest.approx(0.10, abs=1e-5)


def test_fcfe_display_is_computed_by_python() -> None:
    policy = POLICIES["AAPL"]
    snapshot = ValuationSnapshot(
        ticker="AAPL",
        formula_id=policy.formula_id,
        model_version=policy.model_version,
        method=policy.method,
        source_document_id="doc",
        source_url="https://www.sec.gov/example",
        source_content_hash="hash",
        financial_as_of="2026-Q2",
        approved_at="2026-08-28",
        currency_symbol="$",
        discount_rate=0.10,
        terminal_growth=policy.terminal_growth,
        cash_flows_per_share=(10.0,) * 5,
    )
    expected = present_value_with_terminal(
        snapshot.cash_flows_per_share,
        rate=0.10,
        terminal_growth=policy.terminal_growth or 0,
    )
    display = calculate_display(
        policy=policy,
        snapshot=snapshot,
        freshness=_fresh("AAPL"),
        current_price=expected,
    )
    assert display.intrinsic_value == pytest.approx(expected)
    assert display.implied_return == pytest.approx(0.10, abs=1e-5)


def test_new_filing_blocks_old_snapshot_value() -> None:
    policy = POLICIES["AAPL"]
    snapshot = ValuationSnapshot(
        ticker="AAPL",
        formula_id=policy.formula_id,
        model_version=policy.model_version,
        method=policy.method,
        source_document_id="old",
        source_url="https://www.sec.gov/example",
        source_content_hash="hash",
        financial_as_of="2026-Q1",
        approved_at="2026-05-01",
        currency_symbol="$",
        discount_rate=0.10,
        terminal_growth=policy.terminal_growth,
        cash_flows_per_share=(10.0,) * 5,
    )
    freshness = _fresh("AAPL", "new")
    freshness = FreshnessResult(
        ticker="AAPL",
        status="new_filing_pending",
        checked_at=freshness.checked_at,
        latest_document=freshness.latest_document,
        valuation_document_id="old",
        reason="new filing",
    )
    display = calculate_display(
        policy=policy,
        snapshot=snapshot,
        freshness=freshness,
        current_price=100.0,
    )
    assert display.intrinsic_value is None
    assert display.status == "new_filing_pending"


def test_berkshire_has_five_year_sotp_irr() -> None:
    policy = POLICIES["BRK.B"]
    snapshot = ValuationSnapshot(
        ticker="BRK.B",
        formula_id=policy.formula_id,
        model_version=policy.model_version,
        method=policy.method,
        source_document_id="doc",
        source_url="https://www.sec.gov/example",
        source_content_hash="hash",
        financial_as_of="2026-Q2",
        approved_at="2026-08-28",
        currency_symbol="$",
        approved_intrinsic_value=500.0,
        sotp_exit_value_per_share=600.0,
    )
    display = calculate_display(
        policy=policy,
        snapshot=snapshot,
        freshness=_fresh("BRK.B"),
        current_price=450.0,
    )
    assert display.intrinsic_value == 500.0
    assert display.implied_return == pytest.approx((600 / 450) ** 0.2 - 1)
    assert display.return_label == "5Y SOTP IRR"


def test_holding_period_irr_includes_distributions() -> None:
    irr = solve_holding_period_irr(
        purchase_price=100.0,
        exit_value=121.0,
        distributions=(0.0, 0.0, 0.0, 0.0, 0.0),
        years=5,
    )
    assert irr == pytest.approx(0.038860, abs=1e-5)


def test_tsm_converts_twd_ordinary_share_to_usd_adr() -> None:
    policy = POLICIES["TSM"]
    snapshot = ValuationSnapshot(
        ticker="TSM",
        formula_id=policy.formula_id,
        model_version=policy.model_version,
        method=policy.method,
        source_document_id="doc",
        source_url="https://www.sec.gov/example",
        source_content_hash="hash",
        financial_as_of="2026-Q2",
        approved_at="2026-08-28",
        currency_symbol="$",
        fx_reporting_per_market=30.0,
        adr_ratio=5.0,
        discount_rate=policy.hurdle_rate,
        terminal_growth=policy.terminal_growth,
        cash_flows_per_share=(10.0,) * 5,
    )
    twd_value = present_value_with_terminal(
        snapshot.cash_flows_per_share,
        rate=policy.hurdle_rate,
        terminal_growth=policy.terminal_growth or 0,
    )
    display = calculate_display(
        policy=policy,
        snapshot=snapshot,
        freshness=_fresh("TSM"),
        current_price=twd_value * 5 / 30,
    )
    assert display.intrinsic_value == pytest.approx(twd_value * 5 / 30)
    assert display.implied_return == pytest.approx(policy.hurdle_rate, abs=1e-5)


def test_discount_rate_cannot_be_changed_by_snapshot() -> None:
    policy = POLICIES["AAPL"]
    snapshot = ValuationSnapshot(
        ticker="AAPL",
        formula_id=policy.formula_id,
        model_version=policy.model_version,
        method=policy.method,
        source_document_id="doc",
        source_url="https://www.sec.gov/example",
        source_content_hash="hash",
        financial_as_of="2026-Q2",
        approved_at="2026-08-28",
        currency_symbol="$",
        discount_rate=0.09,
        terminal_growth=policy.terminal_growth,
        cash_flows_per_share=(10.0,) * 5,
    )
    display = calculate_display(
        policy=policy,
        snapshot=snapshot,
        freshness=_fresh("AAPL"),
        current_price=100.0,
    )
    assert display.intrinsic_value is None
    assert display.status == "manual_review"
    assert display.warnings == ("底稿折现率越权修改",)
