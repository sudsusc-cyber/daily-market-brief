"""
状态迁移规则引擎（纯函数，无 LLM 依赖）。

每天在 evidence 写入完成后跑一次。执行顺序：迁移 → cap → 事件生成。

修订要点（V1.1）：
- is_strong_support：仅 direction==support ∧ strength>=4
- last_strong_evidence_date 仅由 support evidence 更新
- stable 出口：stable→core（reactivated）、stable→dormant
- core→stable 联合条件：半 cadence 无 strong support AND 90d evidence < 5
- core→dormant 用 elif（与 stable 互斥）
- has_authoritative_source 用 section ∈ {berkshire, frontier_labs} + source_name 域名白名单
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from .cadence import resolve_cadence, stale_days
from .models import Horizon, ThesisEvent, ThesisEvidence, ThesisState

logger = logging.getLogger("thesis.rules")

# 权威源白名单
AUTHORITATIVE_SECTIONS: set[str] = {"berkshire", "frontier_labs"}
AUTHORITATIVE_NAMES: set[str] = {
    # 公司官方：display name + 域名都列
    "openai", "openai.com",
    "anthropic", "anthropic.com",
    "microsoft blog", "microsoft.com",
    "google", "google.com", "deepmind",
    "nvidia", "nvidia.com",
    "tsmc", "tsmc.com",
    "amd ir", "amd.com",
    "apple", "apple.com",
    # IR / 财报 / 监管
    "sec", "sec.gov",
    "berkshire hathaway", "berkshirehathaway.com",
    # 高质量长篇媒体
    "financial times", "ft.com",
    "wall street journal", "wsj", "wsj.com",
    "new york times", "nytimes.com",
    "the economist", "economist.com",
    "reuters",
    "bloomberg",
    # 注：关键人物 score >= 5 的白名单由 figure 模块自己管理，
    # 不在此处重复列出，避免双重维护
}

CORE_CAP = 12
COOLDOWN_DAYS = 30

EMERGING_MIN_EVENTS = 3
EMERGING_MIN_SOURCES = 2

CORE_MIN_ELAPSED_DAYS = 60
CORE_MIN_EVENTS_90D = 5


# ─── predicates ─────────────────────────────────────────────────────


def is_strong_support(e: ThesisEvidence) -> bool:
    """渐明事件的唯一触发条件来源。"""
    return e.strength >= 4 and e.direction == "support"


def days_since(date_str: str | None, today: date) -> int | None:
    if not date_str:
        return None
    try:
        d = date.fromisoformat(date_str)
    except (ValueError, TypeError):
        return None
    return (today - d).days


def _parse_date(s: str) -> date:
    return date.fromisoformat(s)


def group_by_theme(evidence_list: list[ThesisEvidence]) -> dict[str, list[ThesisEvidence]]:
    groups: dict[str, list[ThesisEvidence]] = {}
    for e in evidence_list:
        groups.setdefault(e.theme, []).append(e)
    return groups


def unique_evidence_count(evidence_list: list[ThesisEvidence]) -> int:
    seen: set[str] = set()
    for e in evidence_list:
        key = e.evidence_id or "|".join([
            e.date,
            e.source_section,
            e.source_name,
            e.url or "",
            e.theme,
            e.text,
        ])
        seen.add(key)
    return len(seen)


def filter_recent(
    evidence_list: list[ThesisEvidence],
    *,
    days: int,
    today: date,
) -> list[ThesisEvidence]:
    cutoff = today - timedelta(days=days)
    return [e for e in evidence_list if _parse_date(e.date) >= cutoff]


def distinct_sources(evidence_list: list[ThesisEvidence]) -> int:
    return len({(e.source_section, e.source_name) for e in evidence_list})


def has_authoritative_source(evidence_list: list[ThesisEvidence]) -> bool:
    """source_name 在权威白名单 OR source_section ∈ {berkshire, frontier_labs}.

    白名单同时支持现有模块输出的显示名（'OpenAI', 'AMD IR', 'Reuters'）
    与域名（'openai.com'），均以 lowercase 子串匹配兼容两类。
    """
    for e in evidence_list:
        if e.source_section in AUTHORITATIVE_SECTIONS:
            return True
        src_lower = (e.source_name or "").lower()
        if any(name in src_lower for name in AUTHORITATIVE_NAMES):
            return True
    return False


def has_risk_evidence(evidence_list: list[ThesisEvidence]) -> bool:
    return any(e.direction == "risk" for e in evidence_list)


def any_new_strong_support_today(evs: list[ThesisEvidence], today: date) -> bool:
    today_str = today.isoformat()
    return any(
        e.date == today_str and is_strong_support(e)
        for e in evs
    )


def cooldown_passed(
    last_displayed: str | None, today: date, *, days: int = COOLDOWN_DAYS,
) -> bool:
    if not last_displayed:
        return True
    d = days_since(last_displayed, today)
    return d is not None and d > days


# ─── state init ────────────────────────────────────────────────────


def init_candidate(
    theme: str,
    evidence_list: list[ThesisEvidence],
    today: date,
) -> ThesisState:
    latest = max(evidence_list, key=lambda e: e.date)
    tickers: list[str] = []
    for e in evidence_list:
        for t in e.related_tickers:
            if t not in tickers:
                tickers.append(t)

    horizon: Horizon = latest.horizon
    cadence = resolve_cadence(theme, horizon)

    strong_support_dates = [e.date for e in evidence_list if is_strong_support(e)]

    return ThesisState(
        theme=theme,
        status="candidate",
        related_tickers=tickers,
        cadence=cadence,
        stale_after_days=stale_days(cadence),
        first_seen=min(e.date for e in evidence_list),
        last_evidence_date=latest.date,
        last_strong_evidence_date=(
            max(strong_support_dates) if strong_support_dates else None
        ),
        evidence_count_total=unique_evidence_count(evidence_list),
        evidence_count_recent_90d=unique_evidence_count(evidence_list),
    )


# ─── event builder ─────────────────────────────────────────────────


def make_substantiate_event(
    st: ThesisState,
    recent_evs: list[ThesisEvidence],
) -> ThesisEvent:
    """用最近 is_strong_support evidence 生成 V1「渐明」事件。"""
    strong = [e for e in recent_evs if is_strong_support(e)]
    best = strong[-1] if strong else recent_evs[-1]

    ticker_str = " / ".join(st.related_tickers[:3])
    headline = (
        f"{ticker_str}：「{st.one_line_thesis or st.theme}」获得新证据支持。"
    )
    if len(headline) < 24:
        headline = (
            f"{ticker_str}：关于 {st.theme} 的判断获得新证据支持，可信度上升。"
        )
    if len(headline) > 42:
        headline = headline[:39] + "…"

    return ThesisEvent(
        kind="substantiate",
        theme=st.theme,
        related_tickers=st.related_tickers,
        headline=headline,
        source_url=best.url,
        source_section=best.source_section,
    )


def substantiate_priority(
    event: ThesisEvent, holdings_tickers: list[str],
) -> tuple[int, int]:
    overlap = len(set(event.related_tickers) & set(holdings_tickers))
    return (overlap, 0)


# ─── core cap enforcement ──────────────────────────────────────────


def enforce_core_cap(state: dict[str, ThesisState], *, cap: int = CORE_CAP) -> int:
    """core 数量超过 cap 时，将最旧/最弱的降级为 stable。返回降级数量。"""
    core_themes = [t for t, s in state.items() if s.status == "core"]
    if len(core_themes) <= cap:
        return 0

    def _sort_key(theme: str) -> tuple[str, int]:
        st = state[theme]
        strong_date = st.last_strong_evidence_date or "1900-01-01"
        return (strong_date, st.evidence_count_recent_90d)

    core_themes.sort(key=_sort_key)
    to_downgrade = core_themes[: len(core_themes) - cap]
    for theme in to_downgrade:
        st = state[theme]
        st.status = "stable"
        logger.info(
            "rules.core_cap_downgrade theme=%s last_strong=%s count_90d=%d",
            theme, st.last_strong_evidence_date, st.evidence_count_recent_90d,
        )
    return len(to_downgrade)


# ─── main engine ───────────────────────────────────────────────────


def run_state_transitions(
    today: date,
    state: dict[str, ThesisState],
    recent_evidence: list[ThesisEvidence],
    *,
    holdings_tickers: list[str] | None = None,
) -> tuple[dict[str, ThesisState], list[ThesisEvent]]:
    """
    严格顺序：迁移 → cap → 事件生成。
    返回 (updated_state, events)。
    """
    by_theme = group_by_theme(recent_evidence)
    today_str = today.isoformat()

    # ── Step 1) 新 theme → candidate ──
    for theme, evs in by_theme.items():
        if theme not in state:
            state[theme] = init_candidate(theme, evs, today)
            logger.info("rules.new_candidate theme=%s ev_count=%d", theme, len(evs))

    # ── Step 2) 状态迁移（不生成 events）──
    for theme, st in state.items():
        theme_evs = by_theme.get(theme, [])
        evs_30d = filter_recent(theme_evs, days=30, today=today)
        evs_90d = filter_recent(theme_evs, days=90, today=today)

        # 更新 counters。evidence_count_total 是累计值，只加今日新增的 unique evidence。
        # 以 last_evidence_date != today 作为幂等守卫：同日重跑不会重复累加。
        # （不能用 max(旧值, 窗口内计数)，因为旧 evidence 超过 90 天移出窗口后，
        #   再有新 evidence 进来会被 max 吞掉。）
        today_evs = [e for e in theme_evs if e.date == today_str]
        today_new = unique_evidence_count(today_evs) if today_evs else 0
        if today_new and st.last_evidence_date != today_str:
            st.evidence_count_total += today_new
        st.evidence_count_recent_90d = unique_evidence_count(evs_90d)
        if theme_evs:
            latest = max(theme_evs, key=lambda e: e.date)
            st.last_evidence_date = latest.date
            # 仅在 direction==support 时更新 last_strong_evidence_date
            strong_support_dates = [e.date for e in theme_evs if is_strong_support(e)]
            if strong_support_dates:
                st.last_strong_evidence_date = max(strong_support_dates)

        if st.status == "candidate":
            _transition_candidate(st, evs_30d, theme)

        elif st.status == "emerging":
            _transition_emerging(st, evs_90d, today, theme)

        elif st.status == "core":
            _transition_core(st, evs_90d, today, theme)

        elif st.status == "stable":
            _transition_stable(st, evs_30d, today, theme)

        elif st.status == "dormant":
            _transition_dormant(st, evs_30d, today, theme)

    # ── Step 3) Core cap（先于事件生成）──
    enforce_core_cap(state, cap=CORE_CAP)

    # ── Step 4) 仅在 cap 之后，core 生成「渐明」事件 ──
    events: list[ThesisEvent] = []
    for theme, st in state.items():
        if st.status != "core":
            continue
        evs_30d = filter_recent(by_theme.get(theme, []), days=30, today=today)
        if (
            any_new_strong_support_today(evs_30d, today)
            and cooldown_passed(st.last_displayed_date, today)
        ):
            events.append(make_substantiate_event(st, evs_30d))
            st.last_displayed_date = today_str
            logger.info(
                "rules.substantiate theme=%s tickers=%s",
                theme, st.related_tickers,
            )

    # ── Step 5) 优先级排序 + top 3 ──
    if holdings_tickers:
        events.sort(
            key=lambda e: substantiate_priority(e, holdings_tickers), reverse=True,
        )
    return state, events[:3]


# ─── per-status transition helpers ─────────────────────────────────


def _transition_candidate(
    st: ThesisState,
    evs_30d: list[ThesisEvidence],
    theme: str,
) -> None:
    if (
        len(evs_30d) >= EMERGING_MIN_EVENTS
        and distinct_sources(evs_30d) >= EMERGING_MIN_SOURCES
        and any(is_strong_support(e) for e in evs_30d)
    ):
        st.status = "emerging"
        best = max(evs_30d, key=lambda e: e.strength)
        st.one_line_thesis = best.why_it_matters[:200]
        logger.info(
            "rules.emerging theme=%s ev_30d=%d sources=%d",
            theme, len(evs_30d), distinct_sources(evs_30d),
        )


def _transition_emerging(
    st: ThesisState,
    evs_90d: list[ThesisEvidence],
    today: date,
    theme: str,
) -> None:
    elapsed = days_since(st.first_seen, today)
    if (
        elapsed is not None
        and elapsed >= CORE_MIN_ELAPSED_DAYS
        and len(evs_90d) >= CORE_MIN_EVENTS_90D
        and has_authoritative_source(evs_90d)
    ):
        st.status = "core"
        logger.info(
            "rules.core theme=%s elapsed=%d ev_90d=%d",
            theme, elapsed, len(evs_90d),
        )


def _transition_core(
    st: ThesisState,
    evs_90d: list[ThesisEvidence],
    today: date,
    theme: str,
) -> None:
    # core → dormant: 长期无任何新 evidence（优先于 stable，elif）
    last_d = days_since(st.last_evidence_date, today)
    if last_d is not None and last_d > st.stale_after_days:
        st.status = "dormant"
        logger.info(
            "rules.dormant theme=%s last_evidence=%s stale_days=%d",
            theme, st.last_evidence_date, st.stale_after_days,
        )
        return

    # core → stable: 半 cadence 无 strong support
    #   且 90d evidence 总数 < 5（持续小流入说明仍 active）
    #   且无 risk evidence
    strong_d = days_since(st.last_strong_evidence_date, today) if st.last_strong_evidence_date else 9999
    if (
        strong_d > st.stale_after_days * 0.5
        and len(evs_90d) < 5
        and not has_risk_evidence(evs_90d)
    ):
        st.status = "stable"
        logger.info(
            "rules.stable theme=%s last_strong=%s threshold=%d ev_90d=%d",
            theme, st.last_strong_evidence_date,
            int(st.stale_after_days * 0.5), len(evs_90d),
        )


def _transition_stable(
    st: ThesisState,
    evs_30d: list[ThesisEvidence],
    today: date,
    theme: str,
) -> None:
    # stable → core (reactivated): 30d 内出现 strength≥4
    if any(e.strength >= 4 for e in evs_30d):
        st.status = "core"
        if any(is_strong_support(e) for e in evs_30d):
            st.last_strong_evidence_date = today.isoformat()
        logger.info("rules.stable_reactivated theme=%s", theme)
        return

    # stable → dormant: 长期完全沉寂
    last_d = days_since(st.last_evidence_date, today)
    if last_d is not None and last_d > st.stale_after_days:
        st.status = "dormant"
        logger.info("rules.stable_to_dormant theme=%s last=%s", theme, st.last_evidence_date)


def _transition_dormant(
    st: ThesisState,
    evs_30d: list[ThesisEvidence],
    today: date,
    theme: str,
) -> None:
    # dormant → core (reactivated): strength≥4
    if any(e.strength >= 4 for e in evs_30d):
        st.status = "core"
        if any(is_strong_support(e) for e in evs_30d):
            st.last_strong_evidence_date = today.isoformat()
        logger.info("rules.reactivated theme=%s", theme)
