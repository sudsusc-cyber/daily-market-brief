"""
构建 judgment_section payload 供模板渲染。

V1 只输出「渐明」事件，最多 3 条。
"""

from __future__ import annotations

from .models import EVENT_LABELS, ThesisEvent


def build_judgment_section(events: list[ThesisEvent]) -> dict | None:
    """将 ThesisEvent 列表转为模板可消费的 dict。空列表 → None。"""
    if not events:
        return None

    items = []
    for e in events[:3]:
        items.append({
            "label": EVENT_LABELS.get(e.kind, e.kind),
            "text": e.headline,
            "url": e.source_url,
        })

    return {"items": items}
