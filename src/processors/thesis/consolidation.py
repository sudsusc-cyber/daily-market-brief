"""One-time production-ledger consolidation and deterministic state replay."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from . import rules
from . import state as thesis_state
from .models import ThesisEvidence, ThesisState
from .theme_taxonomy import canonicalize_theme, make_evidence_id

logger = logging.getLogger("thesis.consolidation")

MIGRATION_VERSION = 2
_MARKER_NAME = "thesis_theme_migration.json"


@dataclass(frozen=True)
class MigrationResult:
    applied: bool
    evidence_count: int
    changed_count: int
    theme_count: int


def _marker_path(state_dir: Path) -> Path:
    return state_dir / _MARKER_NAME


def _migration_already_applied(state_dir: Path) -> bool:
    path = _marker_path(state_dir)
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return False
        return int(payload.get("version", 0)) >= MIGRATION_VERSION
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        return False


def _consolidate_evidence(
    evidence_list: list[ThesisEvidence],
) -> tuple[list[ThesisEvidence], int]:
    changed = 0
    unique: dict[str, ThesisEvidence] = {}
    for ev in evidence_list:
        canonical = canonicalize_theme(ev.theme)
        if canonical != ev.theme:
            ev.theme = canonical
            ev.evidence_id = make_evidence_id(ev)
            changed += 1
        unique.setdefault(ev.evidence_id, ev)
    return sorted(unique.values(), key=lambda item: (item.date, item.evidence_id)), changed


def replay_state(
    evidence_list: list[ThesisEvidence],
    *,
    through: date,
) -> dict[str, ThesisState]:
    """按历史日期重放 evidence，重建完整状态迁移与 rolling evidence。"""
    usable = [ev for ev in evidence_list if ev.date <= through.isoformat()]
    evidence_dates = sorted({ev.date for ev in usable})
    rebuilt: dict[str, ThesisState] = {}

    for evidence_date in evidence_dates:
        replay_day = date.fromisoformat(evidence_date)
        cutoff = (replay_day - timedelta(days=90)).isoformat()
        window = [
            ev for ev in usable
            if cutoff <= ev.date <= evidence_date
        ]
        rebuilt, _ = rules.run_state_transitions(
            today=replay_day,
            state=rebuilt,
            recent_evidence=window,
        )

        today_by_theme: dict[str, list[ThesisEvidence]] = {}
        for ev in window:
            if ev.date == evidence_date:
                today_by_theme.setdefault(ev.theme, []).append(ev)
        for theme, new_evidence in today_by_theme.items():
            thesis_state.update_rolling_evidence(rebuilt[theme], new_evidence)

    # The historical production emails never displayed these themes.  Do not
    # inherit a simulated cooldown from replay; the next genuine strong update
    # may be marked as new.
    for st in rebuilt.values():
        st.last_displayed_date = None
    return rebuilt


def migrate_history_if_needed(
    state_dir: Path,
    *,
    today: date,
) -> MigrationResult:
    """Consolidate aliases and replay state exactly once per migration version."""
    if _migration_already_applied(state_dir):
        existing = thesis_state.load_state(state_dir)
        return MigrationResult(False, 0, 0, len(existing))

    evidence = thesis_state.load_all_evidence(state_dir)
    consolidated, changed = _consolidate_evidence(evidence)
    rebuilt = replay_state(consolidated, through=today)

    thesis_state.replace_all_evidence(consolidated, state_dir)
    thesis_state.save_state(rebuilt, state_dir)

    marker = _marker_path(state_dir)
    marker.parent.mkdir(parents=True, exist_ok=True)
    tmp = marker.with_suffix(".tmp")
    tmp.write_text(json.dumps({
        "version": MIGRATION_VERSION,
        "applied_at": today.isoformat(),
        "evidence_count": len(consolidated),
        "changed_count": changed,
        "theme_count": len(rebuilt),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, marker)

    logger.info(
        "consolidation.applied version=%d evidence=%d changed=%d themes=%d",
        MIGRATION_VERSION, len(consolidated), changed, len(rebuilt),
    )
    return MigrationResult(True, len(consolidated), changed, len(rebuilt))
