"""构建持续可见的长期判断区块，当日强证据只作为更新标记。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .models import ThesisEvent, ThesisState

# thesis 字段为兜底准备：匹配旧 headline 格式 "「thesis」tail"（可带 ticker 前缀）
_HEADLINE_FALLBACK_RE = re.compile(r"^(?:[^：]+：)?「([^」]+)」(.+)$")


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


def build_judgment_section(
    events: list[ThesisEvent],
    *,
    state: dict[str, ThesisState] | None = None,
) -> JudgmentSection | None:
    """持续展示 core/emerging/stable 判断；事件仅标注「新证据」。"""
    event_by_theme = {event.theme: event for event in events}
    items: list[dict[str, Any]] = []

    if state:
        status_rank = {"core": 0, "emerging": 1, "stable": 2}
        active = [
            st for st in state.values()
            if st.status in status_rank and st.one_line_thesis.strip()
        ]
        active.sort(key=lambda st: st.theme)
        active.sort(key=lambda st: st.last_evidence_date, reverse=True)
        active.sort(key=lambda st: st.evidence_count_recent_90d, reverse=True)
        active.sort(key=lambda st: st.theme not in event_by_theme)
        active.sort(key=lambda st: status_rank[st.status])

        for st in active[:3]:
            event = event_by_theme.get(st.theme)
            items.append({
                "theme": st.theme,
                "thesis": st.one_line_thesis.strip(),
                "updated": event is not None,
                "url": event.source_url if event else None,
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
            "url": event.source_url,
        })
        included.add(event.theme)

    return JudgmentSection(items=items) if items else None
