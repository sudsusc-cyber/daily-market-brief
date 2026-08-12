"""test_renderer.py — judgment_section 构建单测"""

from datetime import date

from src.processors.thesis.models import (
    ThesisEvent,
    ThesisEvidence,
    ThesisState,
)
from src.processors.thesis.renderer import build_judgment_section

_WEEKDAY = date(2026, 8, 5)
_SATURDAY = date(2026, 8, 8)


def make_event(theme="test-theme", headline="「测试断言」获得新证据支持。",
               thesis="测试断言", tail="获得新证据支持。",
               tickers=None, url=None):
    return ThesisEvent(
        kind="substantiate",
        theme=theme,
        related_tickers=tickers or ["TEST"],
        headline=headline,
        thesis=thesis,
        tail=tail,
        source_url=url,
        source_section="company_news",
    )


def make_state(theme="test-theme", status="core", thesis="测试断言",
               last="2026-08-05", count=5, changed=None):
    return ThesisState(
        theme=theme,
        status=status,
        related_tickers=["TEST"],
        cadence="quarterly",
        stale_after_days=180,
        first_seen="2026-05-01",
        last_evidence_date=last,
        evidence_count_recent_90d=count,
        one_line_thesis=thesis,
        last_state_change_date=changed,
    )


def make_evidence(
    theme="test-theme",
    direction="risk",
    strength=4,
    url=None,
    on=_WEEKDAY,
):
    return ThesisEvidence(
        evidence_id=f"{theme}-{direction}",
        date=on.isoformat(),
        source_section="company_news",
        source_name="Reuters",
        url=url,
        related_tickers=["TEST"],
        theme=theme,
        direction=direction,
        strength=strength,
        horizon="multi_year",
        text="当日重大证据",
        why_it_matters="这是一条需要跟踪的长期判断",
    )


def test_empty_returns_none():
    assert build_judgment_section([]) is None


def test_single_event():
    e = make_event()
    result = build_judgment_section([e])
    assert result is not None
    assert len(result.items) == 1
    assert result.items[0]["thesis"] == "测试断言"
    assert result.items[0]["updated"] is True
    assert result.items[0]["marker"] == "新证据"


def test_two_events():
    events = [
        make_event(theme="t1", thesis="断言1"),
        make_event(theme="t2", thesis="断言2"),
    ]
    result = build_judgment_section(events)
    assert result is not None
    assert len(result.items) == 2


def test_truncate_to_three():
    events = [
        make_event(theme=f"t{i}", thesis=f"断言{i}")
        for i in range(1, 6)
    ]
    result = build_judgment_section(events)
    assert result is not None
    assert len(result.items) == 3
    assert result.items[0]["thesis"] == "断言1"
    assert result.items[2]["thesis"] == "断言3"


def test_url_passthrough():
    e = make_event(url="https://example.com/news")
    result = build_judgment_section([e])
    assert result.items[0]["url"] == "https://example.com/news"


def test_none_url():
    e = make_event(url=None)
    result = build_judgment_section([e])
    assert result.items[0]["url"] is None


def test_weekday_unchanged_core_is_hidden():
    state = {"test-theme": make_state()}

    assert build_judgment_section([], state=state, today=_WEEKDAY) is None


def test_saturday_weekly_review_renders_unchanged_core():
    state = {"test-theme": make_state()}

    result = build_judgment_section([], state=state, today=_SATURDAY)

    assert result is not None
    assert result.items == [{
        "theme": "test-theme",
        "thesis": "测试断言",
        "updated": False,
        "marker": "",
        "url": None,
    }]


def test_event_marks_persistent_item_updated():
    event = make_event(url="https://example.com/update")
    state = {"test-theme": make_state()}

    result = build_judgment_section([event], state=state, today=_WEEKDAY)

    assert result is not None
    assert result.items[0]["updated"] is True
    assert result.items[0]["marker"] == "新证据"
    assert result.items[0]["url"] == "https://example.com/update"


def test_material_risk_is_not_rendered_in_email():
    state = {"test-theme": make_state()}
    evidence = make_evidence(url="https://example.com/risk")

    result = build_judgment_section(
        [], state=state, evidence_today=[evidence], today=_WEEKDAY,
    )

    assert result is None


def test_material_new_variable_renders_emerging_theme_immediately():
    state = {
        "test-theme": make_state(status="emerging"),
    }
    evidence = make_evidence(direction="new_variable")

    result = build_judgment_section(
        [], state=state, evidence_today=[evidence], today=_WEEKDAY,
    )

    assert result is not None
    assert result.items[0]["marker"] == "新变量"


def test_risk_is_hidden_but_new_variable_still_renders():
    state = {"test-theme": make_state()}
    risk = make_evidence(url="https://example.com/risk")
    variable = make_evidence(
        direction="new_variable",
        url="https://example.com/variable",
    )

    result = build_judgment_section(
        [],
        state=state,
        evidence_today=[risk, variable],
        today=_WEEKDAY,
    )

    assert result is not None
    assert result.items[0]["marker"] == "新变量"
    assert result.items[0]["url"] == "https://example.com/variable"


def test_material_risk_is_hidden_during_saturday_review():
    state = {"test-theme": make_state()}
    evidence = make_evidence(on=_SATURDAY)

    assert build_judgment_section(
        [], state=state, evidence_today=[evidence], today=_SATURDAY,
    ) is None


def test_weak_risk_does_not_create_weekday_noise():
    state = {"test-theme": make_state()}
    evidence = make_evidence(strength=3)

    assert build_judgment_section(
        [], state=state, evidence_today=[evidence], today=_WEEKDAY,
    ) is None


def test_new_core_renders_on_transition_day():
    state = {
        "test-theme": make_state(changed=_WEEKDAY.isoformat()),
    }

    result = build_judgment_section([], state=state, today=_WEEKDAY)

    assert result is not None
    assert result.items[0]["marker"] == "新核心"


def test_candidate_and_dormant_are_not_persistently_rendered():
    state = {
        "candidate": make_state("candidate", status="candidate"),
        "dormant": make_state("dormant", status="dormant"),
    }

    assert build_judgment_section([], state=state, today=_SATURDAY) is None


def test_persistent_items_prioritize_core_then_emerging():
    state = {
        "emerging": make_state("emerging", status="emerging", thesis="端倪"),
        "stable": make_state("stable", status="stable", thesis="稳定", count=20),
        "core": make_state("core", status="core", thesis="核心", count=3),
    }

    result = build_judgment_section([], state=state, today=_SATURDAY)

    assert result is not None
    assert [item["thesis"] for item in result.items] == ["核心", "端倪", "稳定"]


def test_fallback_from_old_headline():
    """旧 headline 格式不含 thesis/tail 字段时，正则切分兜底。"""
    e = ThesisEvent(
        kind="substantiate",
        theme="old-theme",
        related_tickers=["T"],
        headline="MSFT：「旧版断言文本」获得新证据支持。",
        source_section="company_news",
    )
    result = build_judgment_section([e])
    assert result is not None
    assert result.items[0]["thesis"] == "旧版断言文本"
    assert result.items[0]["updated"] is True
    assert result.items[0]["marker"] == "新证据"


def test_fallback_no_match_uses_whole_headline():
    """headline 无法切分时，thesis 留空，整句作为 tail。"""
    e = ThesisEvent(
        kind="substantiate",
        theme="bad",
        related_tickers=["T"],
        headline="完全无法解析的旧格式句子",
        source_section="company_news",
    )
    result = build_judgment_section([e])
    assert result is not None
    assert result.items[0]["thesis"] == "完全无法解析的旧格式句子"
