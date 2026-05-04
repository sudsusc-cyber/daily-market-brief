"""test_rules.py — 状态迁移规则单测（纯规则，无 LLM）"""

from datetime import date, timedelta

from src.processors.thesis.models import ThesisEvidence, ThesisState
from src.processors.thesis.rules import (
    any_new_strong_support_today,
    cooldown_passed,
    distinct_sources,
    enforce_core_cap,
    filter_recent,
    group_by_theme,
    has_authoritative_source,
    has_risk_evidence,
    is_strong_support,
    run_state_transitions,
)

# ─── helpers ───────────────────────────────────────────────────────

def _ev(date_str, theme, strength=3, direction="support", source_section="company_news",
        source_name="Reuters", tickers=None, horizon="multi_year"):
    return ThesisEvidence(
        evidence_id="",
        date=date_str,
        source_section=source_section,
        source_name=source_name,
        url=None,
        related_tickers=tickers or ["TEST"],
        theme=theme,
        direction=direction,
        strength=strength,
        horizon=horizon,
        text=f"Evidence for {theme}",
        why_it_matters=f"Impacts {theme}",
    )


def _d(days_ago: int) -> str:
    return (date.today() - timedelta(days=days_ago)).isoformat()


# ─── is_strong_support ──────────────────────────────────────────────


def test_is_strong_support_true():
    e = _ev(_d(0), "x", strength=4, direction="support")
    assert is_strong_support(e) is True


def test_is_strong_support_strength_3_false():
    e = _ev(_d(0), "x", strength=3, direction="support")
    assert is_strong_support(e) is False


def test_is_strong_support_risk_false():
    """strength>=4 但 direction=risk → 不算 strong support"""
    e = _ev(_d(0), "x", strength=5, direction="risk")
    assert is_strong_support(e) is False


def test_is_strong_support_neutral_false():
    e = _ev(_d(0), "x", strength=4, direction="neutral")
    assert is_strong_support(e) is False


# ─── utility functions ─────────────────────────────────────────────


def test_group_by_theme():
    evs = [_ev(_d(0), "a"), _ev(_d(1), "a"), _ev(_d(0), "b")]
    groups = group_by_theme(evs)
    assert len(groups["a"]) == 2
    assert len(groups["b"]) == 1


def test_filter_recent():
    today = date.today()
    evs = [_ev(_d(0), "x"), _ev(_d(60), "x")]
    result = filter_recent(evs, days=30, today=today)
    assert len(result) == 1


def test_distinct_sources():
    evs = [
        _ev(_d(0), "x", source_section="company_news", source_name="A"),
        _ev(_d(1), "x", source_section="company_news", source_name="A"),  # same
        _ev(_d(2), "x", source_section="voices", source_name="B"),         # different
    ]
    assert distinct_sources(evs) == 2


def test_has_authoritative_source_berkshire():
    evs = [_ev(_d(0), "x", source_section="berkshire")]
    assert has_authoritative_source(evs) is True


def test_has_authoritative_source_frontier_labs():
    evs = [_ev(_d(0), "x", source_section="frontier_labs")]
    assert has_authoritative_source(evs) is True


def test_has_authoritative_source_domain():
    evs = [_ev(_d(0), "x", source_section="voices", source_name="sec.gov filing")]
    assert has_authoritative_source(evs) is True


def test_no_authoritative_source():
    evs = [_ev(_d(0), "x", source_section="company_news", source_name="Random Blog")]
    assert has_authoritative_source(evs) is False


def test_authoritative_source_matches_display_name():
    evs = [_ev(_d(0), "x", source_name="OpenAI")]
    assert has_authoritative_source(evs) is True


def test_authoritative_source_matches_domain():
    evs = [_ev(_d(0), "x", source_name="openai.com")]
    assert has_authoritative_source(evs) is True


def test_authoritative_source_matches_amd_ir():
    evs = [_ev(_d(0), "x", source_name="AMD IR")]
    assert has_authoritative_source(evs) is True


def test_authoritative_source_matches_reuters():
    evs = [_ev(_d(0), "x", source_name="Reuters")]
    assert has_authoritative_source(evs) is True


def test_authoritative_source_matches_bloomberg():
    evs = [_ev(_d(0), "x", source_name="Bloomberg")]
    assert has_authoritative_source(evs) is True


def test_authoritative_source_rejects_unknown():
    evs = [_ev(_d(0), "x", source_name="some-blog-i-dont-trust")]
    assert has_authoritative_source(evs) is False


def test_authoritative_source_case_insensitive():
    evs = [_ev(_d(0), "x", source_name="REUTERS")]
    assert has_authoritative_source(evs) is True


def test_has_risk_evidence():
    evs = [
        _ev(_d(0), "x", direction="support"),
        _ev(_d(1), "x", direction="risk"),
    ]
    assert has_risk_evidence(evs) is True


def test_no_risk_evidence():
    evs = [_ev(_d(0), "x", direction="support"), _ev(_d(1), "x", direction="neutral")]
    assert has_risk_evidence(evs) is False


def test_any_new_strong_support_today():
    today = date.today()
    evs = [
        _ev(today.isoformat(), "x", strength=5, direction="support"),
        _ev(_d(1), "x", strength=5, direction="support"),
    ]
    assert any_new_strong_support_today(evs, today) is True


def test_no_new_strong_support_today():
    today = date.today()
    evs = [_ev(_d(1), "x", strength=5, direction="support")]
    assert any_new_strong_support_today(evs, today) is False


def test_strong_risk_today_not_counted():
    """strength=5 但 direction=risk → 不算 strong support today"""
    today = date.today()
    evs = [_ev(today.isoformat(), "x", strength=5, direction="risk")]
    assert any_new_strong_support_today(evs, today) is False


def test_cooldown_passed_none():
    assert cooldown_passed(None, date.today()) is True


def test_cooldown_passed_after_31_days():
    today = date.today()
    long_ago = (today - timedelta(days=31)).isoformat()
    assert cooldown_passed(long_ago, today) is True


def test_cooldown_not_passed_within_30_days():
    today = date.today()
    recent = (today - timedelta(days=15)).isoformat()
    assert cooldown_passed(recent, today) is False


def test_cooldown_not_passed_at_exactly_30():
    """30 天边界:需要 > 30 天才能触发"""
    today = date.today()
    exactly_30 = (today - timedelta(days=30)).isoformat()
    assert cooldown_passed(exactly_30, today) is False


# ─── state transitions ─────────────────────────────────────────────


def test_candidate_to_emerging():
    """candidate: 30d 内 3 条 + 2 source + 1×is_strong_support → emerging"""
    today = date.today()
    state = {
        "test": ThesisState(
            theme="test", status="candidate", related_tickers=["TEST"],
            cadence="quarterly", stale_after_days=180,
            first_seen=_d(20), last_evidence_date=today.isoformat(),
        )
    }
    evs = [
        _ev(_d(5), "test", strength=4, direction="support", source_name="A"),
        _ev(_d(10), "test", strength=3, direction="support", source_name="B"),
        _ev(_d(15), "test", strength=3, direction="support", source_name="A"),
    ]
    new_state, events = run_state_transitions(today, state, evs)
    assert new_state["test"].status == "emerging"


def test_candidate_not_emerging_all_risk():
    """candidate: 3 条 2 source 但全是 risk → 不晋级（无 is_strong_support）"""
    today = date.today()
    state = {
        "test": ThesisState(
            theme="test", status="candidate", related_tickers=["TEST"],
            cadence="quarterly", stale_after_days=180,
            first_seen=_d(20), last_evidence_date=today.isoformat(),
        )
    }
    evs = [
        _ev(_d(5), "test", strength=4, direction="risk", source_name="A"),
        _ev(_d(10), "test", strength=4, direction="risk", source_name="B"),
        _ev(_d(15), "test", strength=3, direction="risk", source_name="A"),
    ]
    new_state, events = run_state_transitions(today, state, evs)
    assert new_state["test"].status == "candidate"


def test_candidate_not_emerging_single_source():
    """candidate: 3 条但只 1 source → 不晋级"""
    today = date.today()
    state = {
        "test": ThesisState(
            theme="test", status="candidate", related_tickers=["TEST"],
            cadence="quarterly", stale_after_days=180,
            first_seen=_d(20), last_evidence_date=today.isoformat(),
        )
    }
    evs = [
        _ev(_d(5), "test", strength=4, direction="support", source_name="A"),
        _ev(_d(10), "test", strength=3, direction="support", source_name="A"),
        _ev(_d(15), "test", strength=3, direction="support", source_name="A"),
    ]
    new_state, events = run_state_transitions(today, state, evs)
    assert new_state["test"].status == "candidate"


def test_candidate_not_emerging_all_strength_3():
    """candidate: 3 条 2 source 但全部 strength=3 → 不晋级"""
    today = date.today()
    state = {
        "test": ThesisState(
            theme="test", status="candidate", related_tickers=["TEST"],
            cadence="quarterly", stale_after_days=180,
            first_seen=_d(20), last_evidence_date=today.isoformat(),
        )
    }
    evs = [
        _ev(_d(5), "test", strength=3, direction="support", source_name="A"),
        _ev(_d(10), "test", strength=3, direction="support", source_name="B"),
        _ev(_d(15), "test", strength=3, direction="support", source_name="A"),
    ]
    new_state, events = run_state_transitions(today, state, evs)
    assert new_state["test"].status == "candidate"


def test_emerging_to_core():
    """emerging: >=60d + 90d 内 5 条 + 权威源 → core"""
    today = date.today()
    state = {
        "test": ThesisState(
            theme="test", status="emerging", related_tickers=["TEST"],
            cadence="quarterly", stale_after_days=180,
            first_seen=_d(65), last_evidence_date=today.isoformat(),
        )
    }
    evs = [
        _ev(_d(5), "test", strength=4, direction="support", source_section="berkshire", source_name="SEC"),
        _ev(_d(15), "test", strength=3),
        _ev(_d(30), "test", strength=3),
        _ev(_d(45), "test", strength=3, source_name="B"),
        _ev(_d(60), "test", strength=4, direction="support"),
    ]
    new_state, events = run_state_transitions(today, state, evs)
    assert new_state["test"].status == "core"


def test_emerging_no_authoritative_no_core():
    """emerging: 满足条数但无权威源 → 不晋级"""
    today = date.today()
    state = {
        "test": ThesisState(
            theme="test", status="emerging", related_tickers=["TEST"],
            cadence="quarterly", stale_after_days=180,
            first_seen=_d(65), last_evidence_date=today.isoformat(),
        )
    }
    evs = [
        _ev(_d(5), "test", strength=4, direction="support", source_section="company_news", source_name="Blog"),
        _ev(_d(15), "test", strength=3, source_name="Blog"),
        _ev(_d(30), "test", strength=3, source_name="Blog"),
        _ev(_d(45), "test", strength=3, source_name="Blog"),
        _ev(_d(60), "test", strength=4, direction="support", source_name="Blog"),
    ]
    new_state, events = run_state_transitions(today, state, evs)
    assert new_state["test"].status == "emerging"


def test_core_to_dormant():
    """core: 超过 stale_after_days 无新 evidence → dormant"""
    today = date.today()
    state = {
        "test": ThesisState(
            theme="test", status="core", related_tickers=["TEST"],
            cadence="fast", stale_after_days=90,
            first_seen=_d(200), last_evidence_date=_d(100),
            last_strong_evidence_date=_d(120),
        )
    }
    new_state, events = run_state_transitions(today, state, [])
    assert new_state["test"].status == "dormant"


def test_core_to_stable():
    """core: 半 cadence 无 strong support + 90d evidence < 5 + 无 risk → stable"""
    today = date.today()
    state = {
        "test": ThesisState(
            theme="test", status="core", related_tickers=["TEST"],
            cadence="fast", stale_after_days=90,
            first_seen=_d(200), last_evidence_date=today.isoformat(),
            last_strong_evidence_date=_d(50),  # > 45 (90*0.5)
        )
    }
    # 90d 内只有 1 条弱 evidence，无 risk
    evs = [_ev(_d(5), "test", strength=3, direction="support")]
    new_state, events = run_state_transitions(today, state, evs)
    assert new_state["test"].status == "stable"


def test_core_not_stable_if_risk():
    """core: 有 risk evidence → 不转 stable，即使 stale"""
    today = date.today()
    state = {
        "test": ThesisState(
            theme="test", status="core", related_tickers=["TEST"],
            cadence="fast", stale_after_days=90,
            first_seen=_d(200), last_evidence_date=today.isoformat(),
            last_strong_evidence_date=_d(50),
        )
    }
    evs = [
        _ev(_d(5), "test", strength=4, direction="risk"),
    ]
    new_state, events = run_state_transitions(today, state, evs)
    assert new_state["test"].status == "core"


def test_core_not_stable_if_enough_evidence():
    """core: 90d evidence >= 5 → 不转 stable，即使无 strong support"""
    today = date.today()
    state = {
        "test": ThesisState(
            theme="test", status="core", related_tickers=["TEST"],
            cadence="fast", stale_after_days=90,
            first_seen=_d(200), last_evidence_date=today.isoformat(),
            last_strong_evidence_date=_d(50),
        )
    }
    evs = [
        _ev(_d(5), "test", strength=3, direction="support"),
        _ev(_d(15), "test", strength=3, direction="support"),
        _ev(_d(25), "test", strength=3, direction="support"),
        _ev(_d(35), "test", strength=3, direction="support"),
        _ev(_d(45), "test", strength=3, direction="support"),
    ]
    new_state, events = run_state_transitions(today, state, evs)
    assert new_state["test"].status == "core"


def test_stable_to_core_reactivated():
    """stable: 30d 内出现 strength>=4 → core"""
    today = date.today()
    state = {
        "test": ThesisState(
            theme="test", status="stable", related_tickers=["TEST"],
            cadence="quarterly", stale_after_days=180,
            first_seen=_d(300), last_evidence_date=_d(100),
            last_strong_evidence_date=_d(150),
        )
    }
    evs = [_ev(today.isoformat(), "test", strength=4, direction="support")]
    new_state, events = run_state_transitions(today, state, evs)
    assert new_state["test"].status == "core"


def test_stable_to_dormant():
    """stable: 长期完全沉寂超过 stale_after_days → dormant"""
    today = date.today()
    state = {
        "test": ThesisState(
            theme="test", status="stable", related_tickers=["TEST"],
            cadence="fast", stale_after_days=90,
            first_seen=_d(300), last_evidence_date=_d(100),
            last_strong_evidence_date=_d(150),
        )
    }
    new_state, events = run_state_transitions(today, state, [])
    assert new_state["test"].status == "dormant"


def test_dormant_reactivated():
    """dormant + strength>=4 evidence → core"""
    today = date.today()
    state = {
        "test": ThesisState(
            theme="test", status="dormant", related_tickers=["TEST"],
            cadence="quarterly", stale_after_days=180,
            first_seen=_d(300), last_evidence_date=_d(200),
            last_strong_evidence_date=_d(250),
        )
    }
    evs = [_ev(today.isoformat(), "test", strength=5, direction="support")]
    new_state, events = run_state_transitions(today, state, evs)
    assert new_state["test"].status == "core"


def test_dormant_reactivated_by_non_support():
    """dormant + strength>=4 但 direction=risk → 仍可 reactivate"""
    today = date.today()
    state = {
        "test": ThesisState(
            theme="test", status="dormant", related_tickers=["TEST"],
            cadence="quarterly", stale_after_days=180,
            first_seen=_d(300), last_evidence_date=_d(200),
            last_strong_evidence_date=_d(250),
        )
    }
    evs = [_ev(today.isoformat(), "test", strength=4, direction="risk")]
    new_state, events = run_state_transitions(today, state, evs)
    assert new_state["test"].status == "core"


def test_new_theme_starts_candidate():
    """全新 theme → candidate"""
    today = date.today()
    state: dict = {}
    evs = [_ev(_d(0), "new-theme", strength=4, direction="support")]
    new_state, events = run_state_transitions(today, state, evs)
    assert "new-theme" in new_state
    assert new_state["new-theme"].status == "candidate"


def test_evidence_count_total_idempotent_on_rerun():
    """同一批 recent evidence 重跑，不应重复累加 evidence_count_total。

    以 last_evidence_date != today 作为幂等守卫：第一次运行时是昨天，正常累加；
    运行后 last_evidence_date 被更新为今天，第二次再跑被守卫拦截。"""
    today = date.today()
    yesterday = (today - timedelta(days=1)).isoformat()
    ev = _ev(today.isoformat(), "test", strength=4, direction="support")
    ev.evidence_id = "same-id"
    state = {
        "test": ThesisState(
            theme="test", status="candidate", related_tickers=["TEST"],
            cadence="quarterly", stale_after_days=180,
            first_seen=_d(20), last_evidence_date=yesterday,
            evidence_count_total=0,
        )
    }

    state, _ = run_state_transitions(today, state, [ev])
    assert state["test"].evidence_count_total == 1

    # 第二次调用：last_evidence_date 已被第一次调用更新为 today，应跳过累加
    state, _ = run_state_transitions(today, state, [ev])
    assert state["test"].evidence_count_total == 1


# ─── substantiate events ───────────────────────────────────────────


def test_core_strong_support_today_triggers_substantiate():
    """core + 当日 is_strong_support evidence → 触发「渐明」事件"""
    today = date.today()
    state = {
        "test": ThesisState(
            theme="test", status="core", related_tickers=["TEST"],
            cadence="quarterly", stale_after_days=180,
            first_seen=_d(100), last_evidence_date=today.isoformat(),
            last_strong_evidence_date=today.isoformat(),
            one_line_thesis="测试判断",
        )
    }
    evs = [_ev(today.isoformat(), "test", strength=5, direction="support")]
    new_state, events = run_state_transitions(today, state, evs)
    assert len(events) >= 1
    assert events[0].kind == "substantiate"


def test_strong_risk_no_substantiate():
    """core + 当日 strength>=4 但 direction=risk → 不触发 substantiate"""
    today = date.today()
    state = {
        "test": ThesisState(
            theme="test", status="core", related_tickers=["TEST"],
            cadence="quarterly", stale_after_days=180,
            first_seen=_d(100), last_evidence_date=today.isoformat(),
            last_strong_evidence_date=today.isoformat(),
            one_line_thesis="测试判断",
        )
    }
    evs = [_ev(today.isoformat(), "test", strength=5, direction="risk")]
    new_state, events = run_state_transitions(today, state, evs)
    assert len(events) == 0


def test_cooldown_blocks_substantiate():
    """30 天内已展示 → 不触发展示"""
    today = date.today()
    state = {
        "test": ThesisState(
            theme="test", status="core", related_tickers=["TEST"],
            cadence="quarterly", stale_after_days=180,
            first_seen=_d(100), last_evidence_date=today.isoformat(),
            last_strong_evidence_date=today.isoformat(),
            last_displayed_date=(today - timedelta(days=10)).isoformat(),
            one_line_thesis="测试判断",
        )
    }
    evs = [_ev(today.isoformat(), "test", strength=5, direction="support")]
    new_state, events = run_state_transitions(today, state, evs)
    assert len(events) == 0


def test_substantiate_max_3():
    """多个 theme 同时触发 → 最多 3 条"""
    today = date.today()
    state = {}
    for i in range(5):
        theme = f"theme-{i}"
        state[theme] = ThesisState(
            theme=theme, status="core", related_tickers=["TEST"],
            cadence="quarterly", stale_after_days=180,
            first_seen=_d(100), last_evidence_date=today.isoformat(),
            last_strong_evidence_date=today.isoformat(),
            one_line_thesis=f"判断{i}",
        )
    evs = [_ev(today.isoformat(), f"theme-{i}", strength=5, direction="support") for i in range(5)]
    new_state, events = run_state_transitions(today, state, evs)
    assert len(events) <= 3


# ─── core cap ──────────────────────────────────────────────────────


def test_core_cap_downgrades_excess():
    """core 超过 12 → 降级多余的为 stable"""
    state = {}
    for i in range(15):
        theme = f"theme-{i}"
        state[theme] = ThesisState(
            theme=theme, status="core" if i < 15 else "candidate",
            related_tickers=["TEST"], cadence="quarterly", stale_after_days=180,
            first_seen=f"2026-{(i % 12) + 1:02d}-01",
            last_evidence_date="2026-05-01",
            last_strong_evidence_date=f"2026-{(i % 12) + 1:02d}-01",
            evidence_count_recent_90d=10 - i,
        )
    downgraded = enforce_core_cap(state, cap=12)
    assert downgraded == 3
    core_count = sum(1 for s in state.values() if s.status == "core")
    assert core_count == 12
