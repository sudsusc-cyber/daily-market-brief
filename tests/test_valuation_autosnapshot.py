from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pandas as pd
import pytest

from src.collectors.stocks import StockSignal
from src.config import COMPANY_HOLDINGS, HOLDINGS
from src.valuation.autosnapshot import AUTO_RULES, build_snapshot, refresh_snapshots
from src.valuation.engine import calculate_display
from src.valuation.models import FreshnessResult, OfficialDocument
from src.valuation.policy import POLICIES


def _holding(ticker: str):
    return next(item for item in HOLDINGS if item.ticker == ticker)


def _signal(ticker: str, price: float = 100.0) -> StockSignal:
    return StockSignal(_holding(ticker), price, 90.0, 80.0, 0.1, 0.2, "NONE")


def _fresh(ticker: str, document_id: str | None = None) -> FreshnessResult:
    now = datetime(2026, 8, 28, tzinfo=UTC)
    document = OfficialDocument(
        document_id=document_id or f"{ticker}-doc",
        document_type="10-K",
        report_period="2025-12-31",
        published_at=now,
        discovered_at=now,
        source_url="https://www.sec.gov/example",
        source_domain="sec.gov",
        content_hash=f"{ticker}-hash",
    )
    return FreshnessResult(
        ticker=ticker,
        status="new_filing_pending",
        checked_at=now,
        latest_document=document,
    )


def _frame(rows: dict[str, list[float]], periods: int = 4) -> pd.DataFrame:
    columns = pd.to_datetime([f"{year}-12-31" for year in range(2025, 2025 - periods, -1)])
    return pd.DataFrame(rows, index=columns).T


class _Ticker:
    def __init__(self) -> None:
        self.cashflow = _frame({"Free Cash Flow": [120.0, 100.0, 90.0, 80.0]})
        self.quarterly_cashflow = _frame({"Free Cash Flow": [30.0, 28.0, 27.0, 25.0]})
        self.financials = _frame(
            {
                "Diluted Average Shares": [10.0, 10.0, 10.0, 10.0],
                "Net Income": [80.0, 70.0, 65.0, 60.0],
                "Pretax Income": [100.0, 90.0, 80.0, 70.0],
                "Tax Provision": [20.0, 18.0, 16.0, 14.0],
            }
        )
        self.quarterly_financials = _frame(
            {
                "Net Income": [20.0, 19.0, 18.0, 17.0],
                "Pretax Income": [25.0, 24.0, 23.0, 22.0],
                "Tax Provision": [5.0, 4.8, 4.6, 4.4],
            }
        )
        self.balance_sheet = _frame(
            {
                "Stockholders Equity": [300.0, 270.0, 245.0, 225.0],
                "Ordinary Shares Number": [10.0, 10.0, 10.0, 10.0],
                "Cash Cash Equivalents And Short Term Investments": [50.0, 45.0, 40.0, 35.0],
                "Total Debt": [30.0, 30.0, 28.0, 25.0],
            }
        )

    def history(self, **_kwargs) -> pd.DataFrame:
        return pd.DataFrame({"Close": [80.0 + index / 10 for index in range(500)]})


def _factory(_symbol: str) -> _Ticker:
    return _Ticker()


@pytest.mark.parametrize("ticker", ["MSFT", "AAPL", "AXP", "BRK.B"])
def test_all_valuation_methods_build_and_recalculate(ticker: str) -> None:
    checked_at = datetime(2026, 8, 28, tzinfo=UTC)
    snapshot = build_snapshot(
        signal=_signal(ticker),
        freshness=_fresh(ticker),
        checked_at=checked_at,
        ticker_factory=_factory,
    )
    assert snapshot.source_document_id == f"{ticker}-doc"
    assert snapshot.source_content_hash == f"{ticker}-hash"
    assert snapshot.formula_id == POLICIES[ticker].formula_id
    assert snapshot.model_version == "2.0"
    assert snapshot.calibration_anchor == "sma_120w"
    assert snapshot.calibration_value == pytest.approx(90.0)
    current = FreshnessResult(
        ticker=ticker,
        status="current",
        checked_at=checked_at,
        latest_document=_fresh(ticker).latest_document,
        valuation_document_id=snapshot.source_document_id,
    )
    display = calculate_display(
        policy=POLICIES[ticker],
        snapshot=snapshot,
        freshness=current,
        current_price=100.0,
    )
    assert display.intrinsic_value is not None
    assert display.intrinsic_value > 0
    assert display.implied_return is not None


def test_refresh_writes_atomic_state_and_keeps_successful_snapshots(tmp_path) -> None:
    checked_at = datetime(2026, 8, 28, tzinfo=UTC)
    signal = _signal("AAPL")
    snapshots, failures = refresh_snapshots(
        signals=[signal],
        freshness={"AAPL": _fresh("AAPL")},
        existing={},
        state_path=tmp_path / "valuation_snapshots.json",
        checked_at=checked_at,
        ticker_factory=_factory,
    )
    assert not failures
    assert "AAPL" in snapshots
    payload = json.loads((tmp_path / "valuation_snapshots.json").read_text())
    assert payload["generated_by"] == "automatic-normalized-financials-v2-cycle-calibrated"
    assert payload["snapshots"][0]["ticker"] == "AAPL"


def test_refresh_retains_last_good_snapshot_when_provider_fails(tmp_path) -> None:
    checked_at = datetime(2026, 8, 28, tzinfo=UTC)
    existing = build_snapshot(
        signal=_signal("AAPL"),
        freshness=_fresh("AAPL"),
        checked_at=checked_at,
        ticker_factory=_factory,
    )

    def failing_factory(_symbol: str):
        return SimpleNamespace(
            cashflow=None,
            quarterly_cashflow=None,
            financials=None,
            quarterly_financials=None,
            balance_sheet=None,
        )

    snapshots, failures = refresh_snapshots(
        signals=[_signal("AAPL")],
        freshness={"AAPL": _fresh("AAPL", "AAPL-new-doc")},
        existing={"AAPL": existing},
        state_path=tmp_path / "valuation_snapshots.json",
        checked_at=checked_at,
        ticker_factory=failing_factory,
    )
    assert snapshots["AAPL"] == existing
    assert "AAPL" in failures


def test_auto_rules_cover_every_holding() -> None:
    assert set(AUTO_RULES) == {holding.ticker for holding in COMPANY_HOLDINGS}


@pytest.mark.parametrize("ticker", ["MSFT", "AAPL", "GOOG"])
def test_mega_tech_is_calibrated_to_120_week_band(ticker: str) -> None:
    snapshot = build_snapshot(
        signal=_signal(ticker),
        freshness=_fresh(ticker),
        checked_at=datetime(2026, 8, 28, tzinfo=UTC),
        ticker_factory=_factory,
    )
    display = calculate_display(
        policy=POLICIES[ticker],
        snapshot=snapshot,
        freshness=FreshnessResult(
            ticker=ticker,
            status="current",
            checked_at=datetime(2026, 8, 28, tzinfo=UTC),
            latest_document=_fresh(ticker).latest_document,
            valuation_document_id=snapshot.source_document_id,
        ),
        current_price=100.0,
    )
    assert display.intrinsic_value is not None
    assert 81.0 - 1e-4 <= display.intrinsic_value <= 99.0 + 1e-4


@pytest.mark.parametrize("ticker", ["NVDA", "TSM"])
def test_fast_growth_is_calibrated_to_250_day_band(ticker: str) -> None:
    snapshot = build_snapshot(
        signal=_signal(ticker),
        freshness=_fresh(ticker),
        checked_at=datetime(2026, 8, 28, tzinfo=UTC),
        ticker_factory=_factory,
    )
    expected_anchor = sum(80.0 + index / 10 for index in range(250, 500)) / 250
    assert snapshot.calibration_anchor == "sma_250d"
    assert snapshot.calibration_value == pytest.approx(expected_anchor)
    assert snapshot.raw_intrinsic_value is not None
