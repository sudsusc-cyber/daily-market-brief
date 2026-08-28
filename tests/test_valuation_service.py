from __future__ import annotations

import json
from datetime import UTC, datetime

from src.collectors.stocks import StockSignal
from src.config import HOLDINGS
from src.valuation.engine import ValuationSnapshot
from src.valuation.models import FreshnessResult, OfficialDocument, ValuationDisplay
from src.valuation.morningstar import MorningstarFairValue
from src.valuation.policy import POLICIES
from src.valuation.service import (
    apply_morningstar_fair_values,
    commit_published_values,
    enforce_jump_guard,
    prepare_valuation_displays,
)


def _display(value: float, document_id: str = "doc") -> ValuationDisplay:
    return ValuationDisplay(
        ticker="AAPL",
        status="current",
        intrinsic_value=value,
        implied_return=0.10,
        source_document_id=document_id,
        model_version="1.0",
    )


def test_morningstar_replaces_value_but_keeps_independent_irr() -> None:
    original = _display(200.0)
    fair_value = MorningstarFairValue(
        ticker="AAPL",
        provider_code="XNAS:AAPL",
        fair_value=290.0,
        currency="USD",
        rating_type="published-research",
        fair_value_updated_at="2026-08-07",
        retrieved_at="2026-08-28T00:00:00+00:00",
        source_provider="Morningstar public research",
        source_url="https://www.morningstar.com/stocks/apple-test",
    )
    result = apply_morningstar_fair_values(
        {"AAPL": original},
        fair_values={"AAPL": fair_value},
        failures={},
    )["AAPL"]
    assert result.intrinsic_value == 290.0
    assert result.implied_return == original.implied_return
    assert result.value_label == "公允价值"
    assert result.currency_symbol == "$"


def test_jump_guard_blocks_unattributed_move(tmp_path) -> None:
    commit_published_values(
        {"AAPL": _display(100.0)},
        state_dir=tmp_path,
        sent_at=datetime(2026, 8, 27, tzinfo=UTC),
    )
    guarded = enforce_jump_guard({"AAPL": _display(104.0)}, state_dir=tmp_path)
    assert guarded["AAPL"].status == "manual_review"
    assert guarded["AAPL"].intrinsic_value is None
    assert "跳变 4.0%" in guarded["AAPL"].warnings[0]


def test_jump_guard_allows_new_official_document(tmp_path) -> None:
    commit_published_values(
        {"AAPL": _display(100.0, "old")},
        state_dir=tmp_path,
        sent_at=datetime(2026, 8, 27, tzinfo=UTC),
    )
    guarded = enforce_jump_guard(
        {"AAPL": _display(120.0, "new")},
        state_dir=tmp_path,
    )
    assert guarded["AAPL"].intrinsic_value == 120.0


def test_commit_keeps_pending_ticker_previous_value(tmp_path) -> None:
    commit_published_values(
        {"AAPL": _display(100.0)},
        state_dir=tmp_path,
        sent_at=datetime(2026, 8, 27, tzinfo=UTC),
    )
    commit_published_values(
        {"AAPL": ValuationDisplay(ticker="AAPL", status="manual_review")},
        state_dir=tmp_path,
        sent_at=datetime(2026, 8, 28, tzinfo=UTC),
    )
    payload = json.loads((tmp_path / "valuation_published.json").read_text())
    assert payload["valuations"]["AAPL"]["intrinsic_value"] == 100.0


def test_prepare_uses_automatic_snapshot_without_pending(monkeypatch, tmp_path) -> None:
    now = datetime(2026, 8, 28, tzinfo=UTC)
    policy = POLICIES["AAPL"]
    document = OfficialDocument(
        document_id="latest",
        document_type="10-Q",
        report_period="2026-06-30",
        published_at=now,
        discovered_at=now,
        source_url="https://www.sec.gov/latest",
        source_domain="sec.gov",
        content_hash="verified",
    )
    initial = FreshnessResult(
        ticker="AAPL",
        status="new_filing_pending",
        checked_at=now,
        latest_document=document,
    )
    snapshot = ValuationSnapshot(
        ticker="AAPL",
        formula_id=policy.formula_id,
        model_version=policy.model_version,
        method=policy.method,
        source_document_id="latest",
        source_url=document.source_url,
        source_content_hash="verified",
        financial_as_of="2026-06-30",
        approved_at="2026-08-28",
        currency_symbol="$",
        discount_rate=policy.hurdle_rate,
        terminal_growth=policy.terminal_growth,
        cash_flows_per_share=(10.0, 10.5, 11.0, 11.5, 12.0),
    )
    holding = next(item for item in HOLDINGS if item.ticker == "AAPL")
    signal = StockSignal(holding, 100.0, 90.0, 80.0, 0.1, 0.2, "NONE")
    monkeypatch.setattr("src.valuation.service.POLICIES", {"AAPL": policy})
    monkeypatch.setattr(
        "src.valuation.service.check_official_freshness",
        lambda *args, **kwargs: {"AAPL": initial},
    )
    monkeypatch.setattr(
        "src.valuation.service.refresh_snapshots",
        lambda **kwargs: ({"AAPL": snapshot}, {}),
    )
    displays, freshness = prepare_valuation_displays(
        signals=[signal],
        state_dir=tmp_path,
        config_dir=tmp_path,
        checked_at=now,
        download_original=True,
    )
    assert freshness["AAPL"].status == "current"
    assert displays["AAPL"].intrinsic_value is not None
    assert not displays["AAPL"].is_pending
