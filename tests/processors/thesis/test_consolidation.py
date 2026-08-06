"""Historical theme consolidation and state replay tests."""

from datetime import date

from src.processors.thesis.consolidation import migrate_history_if_needed
from src.processors.thesis.models import ThesisEvidence
from src.processors.thesis.state import (
    append_evidence,
    load_all_evidence,
    load_state,
)
from src.processors.thesis.theme_taxonomy import make_evidence_id


def _evidence(
    evidence_date: str,
    theme: str,
    source: str,
    strength: int,
    ticker: str,
) -> ThesisEvidence:
    ev = ThesisEvidence(
        evidence_id="",
        date=evidence_date,
        source_section="company_news",
        source_name=source,
        url=f"https://example.com/{evidence_date}/{theme}",
        related_tickers=[ticker],
        theme=theme,
        direction="support",
        strength=strength,
        horizon="multi_year",
        text=f"{theme} evidence on {evidence_date}",
        why_it_matters="AI基础设施需求的长期增长趋势持续得到印证",
    )
    ev.evidence_id = make_evidence_id(ev)
    return ev


def test_migration_consolidates_aliases_and_replays_to_core(tmp_path):
    through = date(2026, 6, 10)
    evidence = [
        _evidence("2026-05-01", "ai-demand-sustained", "Yahoo", 4, "NVDA"),
        _evidence("2026-05-10", "ai-enterprise-adoption", "Benzinga", 3, "MSFT"),
        _evidence("2026-05-20", "ai-capex-cycle", "Reuters", 4, "GOOG"),
        _evidence("2026-06-01", "ai-agent-compute-demand", "Yahoo", 3, "NVDA"),
        _evidence("2026-06-10", "ai-infrastructure-leasing", "NVIDIA", 4, "TSM"),
    ]
    append_evidence(evidence, tmp_path, today=through)

    result = migrate_history_if_needed(tmp_path, today=through)

    assert result.applied is True
    assert result.evidence_count == 5
    assert result.changed_count == 5
    assert result.theme_count == 1
    assert {ev.theme for ev in load_all_evidence(tmp_path)} == {
        "ai-infrastructure-demand",
    }
    rebuilt = load_state(tmp_path)
    assert list(rebuilt) == ["ai-infrastructure-demand"]
    assert rebuilt["ai-infrastructure-demand"].status == "core"
    assert rebuilt["ai-infrastructure-demand"].evidence_count_total == 5
    assert rebuilt["ai-infrastructure-demand"].last_displayed_date is None
    assert set(rebuilt["ai-infrastructure-demand"].related_tickers) == {
        "NVDA", "MSFT", "GOOG", "TSM",
    }


def test_migration_marker_makes_repeat_a_noop(tmp_path):
    through = date(2026, 6, 10)
    append_evidence([
        _evidence("2026-05-01", "ai-demand", "Reuters", 4, "NVDA"),
    ], tmp_path, today=through)

    first = migrate_history_if_needed(tmp_path, today=through)
    second = migrate_history_if_needed(tmp_path, today=through)

    assert first.applied is True
    assert second.applied is False
    assert second.theme_count == 1
