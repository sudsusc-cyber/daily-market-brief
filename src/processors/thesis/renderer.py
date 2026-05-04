"""
构建 judgment_section payload 供模板渲染。

V1 只输出「渐明」事件，最多 3 条。
模板将 thesis 与 tail 分开渲染：「thesis」染 accent 色，tail 正文色。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .models import ThesisEvent

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


def build_judgment_section(events: list[ThesisEvent]) -> JudgmentSection | None:
    """将 ThesisEvent 列表转为模板可消费的 dataclass。空列表 → None。"""
    if not events:
        return None

    items = []
    for e in events[:3]:
        thesis, tail = _resolve_thesis_tail(e)
        items.append({
            "thesis": thesis,
            "tail": tail,
            "url": e.source_url,
        })

    return JudgmentSection(items=items)
