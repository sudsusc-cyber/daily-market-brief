"""按事件触发长期判断，并在周六固定做每周回顾。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from .models import ThesisEvent, ThesisEvidence, ThesisState

# thesis 字段为兜底准备：匹配旧 headline 格式 "「thesis」tail"（可带 ticker 前缀）
_HEADLINE_FALLBACK_RE = re.compile(r"^(?:[^：]+：)?「([^」]+)」(.+)$")
_MATERIAL_STRENGTH = 4
_MARKER_PRIORITY = {
    "风险": 0,
    "新核心": 1,
    "新变量": 2,
    "新证据": 3,
}


@dataclass
class JudgmentSection:
    items: list[dict[str, Any]]


def _resolve_thesis_tail(event: ThesisEvent) -> tuple[str, str]:
    """返回 (thesis, tail)。优先使用新字段，失败时从 headline 正则切分兜底。"""
    if event.thesis and event.tail:
        return event.thesis, event.tail
    m = _HEADLINE_FALLBACK_RE.match(event.headline)
    if m:
        return m.group(1), m.group(2)
    return "", event.headline


def _set_marker(markers: dict[str, str], theme: str, marker: str) -> bool:
    existing = markers.get(theme)
    if existing is None or _MARKER_PRIORITY[marker] < _MARKER_PRIORITY[existing]:
        markers[theme] = marker
        return True
    return False


def _build_markers(
    events: list[ThesisEvent],
    state: dict[str, ThesisState],
    evidence_today: list[ThesisEvidence],
    today: date,
) -> tuple[dict[str, str], dict[str, str | None]]:
    markers: dict[str, str] = {}
    urls: dict[str, str | None] = {}
    today_str = today.isoformat()

    # 重大 risk / new_variable 不受 support cooldown 限制。
    for evidence in evidence_today:
        if evidence.date != today_str or evidence.strength < _MATERIAL_STRENGTH:
            continue
        if evidence.direction == "risk":
            if _set_marker(markers, evidence.theme, "风险"):
                urls[evidence.theme] = evidence.url
        elif (
            evidence.direction == "new_variable"
            and _set_marker(markers, evidence.theme, "新变量")
        ):
            urls[evidence.theme] = evidence.url

    for theme, thesis_state in state.items():
        if (
            thesis_state.status == "core"
            and thesis_state.last_state_change_date == today_str
            and _set_marker(markers, theme, "新核心")
        ):
            urls[theme] = None

    # substantiate 事件已由 rules.py 执行 21 天 cooldown。
    for event in events:
        if _set_marker(markers, event.theme, "新证据"):
            urls[event.theme] = event.source_url
    return markers, urls


def build_judgment_section(
    events: list[ThesisEvent],
    *,
    state: dict[str, ThesisState] | None = None,
    evidence_today: list[ThesisEvidence] | None = None,
    today: date | None = None,
) -> JudgmentSection | None:
    """平日仅展示有变化的判断；周六回顾最重要的 3 条。"""
    if today is None:
        today = date.today()
    if evidence_today is None:
        evidence_today = []

    event_by_theme = {event.theme: event for event in events}
    items: list[dict[str, Any]] = []

    if state:
        markers, marker_urls = _build_markers(
            events,
            state,
            evidence_today,
            today,
        )
        weekly_review = today.weekday() == 5
        status_rank = {"core": 0, "emerging": 1, "stable": 2}
        active = [
            st for st in state.values()
            if st.status in status_rank and st.one_line_thesis.strip()
            and (weekly_review or st.theme in markers)
        ]
        active.sort(key=lambda st: st.theme)
        active.sort(key=lambda st: st.last_evidence_date, reverse=True)
        active.sort(key=lambda st: st.evidence_count_recent_90d, reverse=True)
        active.sort(key=lambda st: status_rank[st.status])
        active.sort(key=lambda st: _MARKER_PRIORITY.get(markers.get(st.theme, ""), 99))

        for st in active[:3]:
            event = event_by_theme.get(st.theme)
            marker = markers.get(st.theme, "")
            items.append({
                "theme": st.theme,
                "thesis": st.one_line_thesis.strip(),
                "updated": bool(marker),
                "marker": marker,
                "url": marker_urls.get(st.theme) or (event.source_url if event else None),
            })

    # Compatibility/fallback: an event should never disappear even if an older
    # caller does not pass state or its state entry is unavailable.
    included = {str(item.get("theme", "")) for item in items}
    for event in events:
        if len(items) >= 3 or event.theme in included:
            continue
        thesis, _ = _resolve_thesis_tail(event)
        items.append({
            "theme": event.theme,
            "thesis": thesis or event.headline,
            "updated": True,
            "marker": "新证据",
            "url": event.source_url,
        })
        included.add(event.theme)

    return JudgmentSection(items=items) if items else None
