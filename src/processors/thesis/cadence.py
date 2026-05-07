"""
Theme → cadence 映射。

规则：
  1. theme 关键字子串匹配 → cadence（关键字和 theme 都是 hyphen-separated）
  2. 未命中 → 按 LLM 给出的 horizon 退化
  3. cadence → stale_after_days 查找表
"""

from __future__ import annotations

from .models import Cadence, Horizon

THEME_CADENCE_HINTS: dict[str, Cadence] = {
    "ai-capex": "quarterly",
    "ai-demand": "quarterly",
    "compute": "quarterly",
    "inference-cost": "quarterly",
    "inference": "quarterly",
    "pricing-power": "quarterly",
    "margin": "quarterly",
    "monetization": "quarterly",
    "ad-cycle": "quarterly",
    "enterprise-revenue": "quarterly",
    "capital-allocation": "slow",
    "buyback": "slow",
    "management": "slow",
    "succession": "slow",
    "regulatory": "fast",
    "antitrust": "fast",
    "geopolitical": "fast",
    "export-control": "fast",
    "brand": "structural",
    "moat": "structural",
    "culture": "structural",
    "industry-structure": "structural",
}

CADENCE_DAYS: dict[Cadence, int] = {
    "fast": 90,
    "quarterly": 180,
    "slow": 360,
    "structural": 720,
}


def resolve_cadence(theme: str, horizon: Horizon) -> Cadence:
    """按 theme 关键字匹配 cadence；未命中时退化到 horizon。"""
    theme_lower = theme.lower()
    for key, cad in THEME_CADENCE_HINTS.items():
        if key in theme_lower:
            return cad
    # 退化
    if horizon == "structural":
        return "structural"
    if horizon == "multi_year":
        return "slow"
    return "quarterly"


def stale_days(cadence: Cadence) -> int:
    return CADENCE_DAYS[cadence]
