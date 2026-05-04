"""
Thesis 模块 dataclass 与类型别名。

状态机: candidate → emerging → core → stable → dormant
（reactivated 不是持久状态，仅是 dormant→core 与 stable→core 的后台 transition label，
用于 log 与调试输出，不进 ThesisState.status 字段）
V1 前台 EventKind: 仅 "substantiate" → 渲染为「渐明」
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Direction = Literal["support", "risk", "neutral", "new_variable"]
Horizon = Literal["quarterly", "multi_year", "structural"]
Cadence = Literal["fast", "quarterly", "slow", "structural"]
Status = Literal["candidate", "emerging", "core", "stable", "dormant"]
EventKind = Literal["substantiate"]

# 状态词中文映射（仅「渐明」导出到模板，其余保留备查）
STATUS_LABELS: dict[Status, str] = {
    "candidate": "候选",
    "emerging": "端倪",
    "core": "核心",
    "stable": "稳定",
    "dormant": "收束",
}

EVENT_LABELS: dict[EventKind, str] = {
    "substantiate": "渐明",
}


@dataclass
class ThesisEvidence:
    evidence_id: str  # sha1 哈希，幂等去重用
    date: str  # YYYY-MM-DD
    source_section: str  # voices / company_news / macro / berkshire / frontier_labs
    source_name: str
    url: str | None
    related_tickers: list[str]
    theme: str  # lowercase, hyphen-separated（已 canonicalize）
    direction: Direction
    strength: int  # 1-5; <3 在抽取阶段丢弃
    horizon: Horizon
    text: str
    why_it_matters: str


@dataclass
class ThesisState:
    theme: str
    status: Status
    related_tickers: list[str]
    cadence: Cadence
    stale_after_days: int
    first_seen: str  # YYYY-MM-DD
    last_evidence_date: str  # YYYY-MM-DD
    last_strong_evidence_date: str | None = None  # strength>=4 AND direction==support
    last_displayed_date: str | None = None  # 30d cooldown
    evidence_count_total: int = 0
    evidence_count_recent_90d: int = 0
    rolling_evidence: list[dict] = field(default_factory=list)  # 最近 20 条摘要
    one_line_thesis: str = ""


@dataclass
class ThesisEvent:
    kind: EventKind
    theme: str
    related_tickers: list[str]
    headline: str  # 完整展示句（= f"「{thesis}」{tail}"），用于日志/兼容
    thesis: str = ""  # 断言文本（不含括号）；渲染时模板硬写「」
    tail: str = ""    # 尾句，固定 "获得新证据支持。"
    source_url: str | None = None
    source_section: str = ""
