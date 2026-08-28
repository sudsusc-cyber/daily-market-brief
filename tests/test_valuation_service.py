from __future__ import annotations

import json
from datetime import UTC, datetime

from src.valuation.models import ValuationDisplay
from src.valuation.service import commit_published_values, enforce_jump_guard


def _display(value: float, document_id: str = "doc") -> ValuationDisplay:
    return ValuationDisplay(
        ticker="AAPL",
        status="current",
        intrinsic_value=value,
        implied_return=0.10,
        source_document_id=document_id,
        model_version="1.0",
    )


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
