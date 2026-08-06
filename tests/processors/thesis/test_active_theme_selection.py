"""test_active_theme_selection.py — main pipeline active theme guardrail"""

from src.main import _ACTIVE_THESIS_THEME_LIMIT, _select_active_thesis_themes
from src.processors.thesis.models import ThesisState


def _state(status: str, last: str) -> ThesisState:
    return ThesisState(
        theme="x",
        status=status,
        related_tickers=["TEST"],
        cadence="quarterly",
        stale_after_days=180,
        first_seen="2026-01-01",
        last_evidence_date=last,
    )


def test_select_active_themes_prioritizes_status_then_recent_date() -> None:
    state = {
        "old-core": _state("core", "2026-01-01"),
        "new-core": _state("core", "2026-05-01"),
        "new-emerging": _state("emerging", "2026-06-01"),
        "stable": _state("stable", "2026-06-02"),
        "candidate": _state("candidate", "2026-06-03"),
    }

    out = _select_active_thesis_themes(state, limit=5)

    assert out == ["new-core", "old-core", "new-emerging", "candidate", "stable"]
    assert "candidate" in out


def test_select_active_themes_caps_length() -> None:
    state = {
        f"theme-{i:03d}": _state("stable", f"2026-05-{(i % 28) + 1:02d}")
        for i in range(_ACTIVE_THESIS_THEME_LIMIT + 10)
    }

    out = _select_active_thesis_themes(state)

    assert len(out) == _ACTIVE_THESIS_THEME_LIMIT
