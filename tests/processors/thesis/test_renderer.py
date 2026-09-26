"""The old unsupported thesis can never reach the publication boundary."""

from copy import deepcopy
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from src.processors.thesis.renderer import (
    JudgmentSection,
    build_judgment_section,
    commit_publications,
    load_publications,
    validate_publication,
)
from src.renderer.render import render_email

TODAY = date(2026, 9, 26)
FACT = "微软计划到2030年在海湾国家投资超过100亿美元用于云和AI基础设施。"


def source(text=FACT, *, url="https://example.com/microsoft", published="2026-09-26", **changes):
    row = dict(
        original_title=text,
        original_summary="",
        excerpt=text,
        output_text=text,
        validated_text=text,
        mode="source_extract",
        url=url,
        published_at=published,
        source_name="Reuters",
    )
    row.update(changes)
    return SimpleNamespace(summary_html=text, footnotes=[SimpleNamespace(url=url)], evidence=[row])


def sources(*objects):
    return {"company_news": list(objects) or [source()]}


def build(*objects, **kwargs):
    return build_judgment_section(sources=sources(*objects), today=TODAY, **kwargs)


def test_real_incident_becomes_bounded_watchpoint_not_sovereign_buyer_claim():
    result = build()
    item = result.items[0]
    assert item["fact"] == FACT
    assert item["thesis"] == "基础设施投入的长期价值取决于资本回报"
    assert "主权资本" not in str(result)
    assert item["url"] == source().evidence[0]["url"]
    assert item["evidence"]["excerpt"] == FACT
    assert item["watch"] == "实际投入、投产进度、利用率与现金流能否匹配。"


@pytest.mark.parametrize(
    "text",
    [
        "微软尚未获监管批准。",
        "微软已获监管批准。",
        "微软计划投资100亿美元建设云基础设施。",
        "微软取消投资100亿美元建设云基础设施的计划。",
        "苹果与高通续签专利许可协议。",
        "微软收入增长10%。",
        "万事达开通稳定币结算。",
        "苹果推出新款手机。",
        "苹果宣布回购计划。",
    ],
)
def test_fact_states_and_subjects_are_preserved_verbatim(text):
    result = build(source(text))
    assert result and result.items[0]["fact"] == text
    assert result.items[0]["marker"] in {"新证据", "新变量"}


@pytest.mark.parametrize(
    "text",
    [
        "主权资本正成为云与算力需求的长期买家。",
        "英伟达处于新的全球AI规则争论的中心。",
        "可口可乐宣布新任北美总裁。",
        "某国将大力发展人工智能。",
        "微软股价今天上涨10%。",
    ],
)
def test_no_irrelevant_or_unbounded_watchpoint(text):
    assert build(source(text)) is None


@pytest.mark.parametrize(
    "change",
    ["body", "footnote", "original", "amount", "negation", "subject", "date", "old", "future"],
)
def test_broken_source_binding_is_rejected(change):
    obj = source()
    row = obj.evidence[0]
    if change == "body":
        obj.summary_html = "没有对应新闻"
    elif change == "footnote":
        obj.footnotes = []
    elif change == "original":
        row["original_title"] = "无关新闻"
    elif change == "amount":
        row["output_text"] = FACT.replace("100", "1000")
    elif change == "negation":
        row["output_text"] = FACT.replace("计划", "已经")
    elif change == "subject":
        row["output_text"] = FACT.replace("微软", "主权基金")
    elif change == "date":
        row["published_at"] = ""
    elif change == "old":
        row["published_at"] = "2020-09-26"
    elif change == "future":
        row["published_at"] = "2026-09-27"
    assert build(obj) is None


def test_legacy_state_events_and_llm_why_never_supply_display_text():
    legacy = SimpleNamespace(
        thesis="主权资本正成为云与算力需求的长期买家", source_url="https://example.com"
    )
    assert (
        build_judgment_section(
            [legacy], state={"old": legacy}, evidence_today=[legacy], today=TODAY
        )
        is None
    )
    obj = source(why_it_matters=legacy.thesis)
    result = build(obj, state={"old": legacy}, evidence_today=[legacy])
    assert legacy.thesis not in str(result)


@pytest.mark.parametrize(
    "field,value",
    [
        ("thesis", "主权资本正成为长期买家"),
        ("fact", FACT.replace("计划", "已经")),
        ("watch", "收入必将翻倍"),
        ("url", "https://example.com/unrelated"),
        ("url", "javascript:alert(1)"),
        ("marker", "新核心"),
        ("section", "宏观视野"),
    ],
)
def test_final_render_gate_rejects_changed_interpretation_or_citation(field, value):
    result = build()
    result.items[0][field] = value
    assert validate_publication(result, sources=sources(), today=TODAY) is None


def test_changed_final_body_cannot_keep_previous_judgment():
    result = build()
    assert validate_publication(result, sources={}, today=TODAY) is None


def test_same_fact_new_url_or_translation_is_not_new_evidence(tmp_path):
    result = build()
    commit_publications(result, tmp_path, today=TODAY)
    other = source(url="https://another.example.com/syndication")
    assert build(other, history=load_publications(tmp_path)) is None
    assert build(source(), history=load_publications(tmp_path)) is None


def test_new_fact_in_same_theme_is_visible_next_day_without_21_day_cooldown(tmp_path):
    first = build()
    commit_publications(first, tmp_path, today=TODAY)
    tomorrow = TODAY + timedelta(days=1)
    second = source(FACT.replace("100", "120"), published=tomorrow.isoformat())
    result = build_judgment_section(
        sources=sources(second), history=load_publications(tmp_path), today=tomorrow
    )
    assert result and result.items[0]["fact"] == second.summary_html


def test_seven_days_of_distinct_news_can_publish_every_day(tmp_path):
    for i in range(7):
        day = TODAY + timedelta(days=i)
        obj = source(FACT.replace("100", str(100 + i)), published=day.isoformat())
        result = build_judgment_section(
            sources=sources(obj), history=load_publications(tmp_path), today=day
        )
        assert result
        commit_publications(result, tmp_path, today=day)
    assert len(load_publications(tmp_path)) == 7


def test_top_three_only_commits_visible_facts_and_can_publish_remaining_later(tmp_path):
    objects = [source(FACT.replace("100", str(100 + i))) for i in range(4)]
    result = build(*objects)
    assert len(result.items) == 3
    commit_publications(result, tmp_path, today=TODAY)
    assert len(load_publications(tmp_path)) == 3
    remaining = build(*objects, history=load_publications(tmp_path))
    assert remaining and len(remaining.items) == 1


def test_no_publication_no_history_write(tmp_path):
    result = build()
    assert result and not load_publications(tmp_path)
    commit_publications(None, tmp_path, today=TODAY)
    assert not (tmp_path / "thesis_publications.json").exists()


def test_corrupt_history_fails_without_overwriting(tmp_path):
    path = tmp_path / "thesis_publications.json"
    path.write_text("broken")
    with pytest.raises(ValueError):
        commit_publications(build(), tmp_path, today=TODAY)
    assert path.read_text() == "broken"


def test_all_visible_sections_bind_to_their_own_source():
    obj = source()
    point = SimpleNamespace(text=FACT, source_url=obj.evidence[0]["url"], evidence=obj.evidence)
    for section, value in [
        ("company_news", obj),
        ("macro", obj),
        ("voices", point),
        ("frontier_labs", point),
    ]:
        result = build_judgment_section(sources={section: [value]}, today=TODAY)
        assert result and result.items[0]["source_section"] == section


def test_render_includes_fact_and_source_but_escapes_untrusted_source_text():
    from datetime import UTC, datetime

    obj = source()
    result = build(obj)
    html = render_email(
        signals=[],
        generated_at=datetime(2026, 9, 26, tzinfo=UTC),
        company_news_summary=obj,
        judgment_section=result,
    )
    assert "本期依据（昨日动态）" not in html and "后续验证" not in html
    assert "基础设施投入的长期价值取决于资本回报" in html
    assert html.count("https://example.com/microsoft") >= 2
    assert "主权资本" not in html
    malicious = deepcopy(result)
    malicious.items[0]["thesis"] = "<script>alert(1)</script>"
    html = render_email(
        signals=[],
        generated_at=datetime(2026, 9, 26, tzinfo=UTC),
        company_news_summary=obj,
        judgment_section=malicious,
    )
    assert "<script>" not in html and "后续验证" not in html


def test_unbound_legacy_payload_cannot_reenter_via_render_email():
    from datetime import UTC, datetime

    html = render_email(
        signals=[],
        generated_at=datetime(2026, 9, 26, tzinfo=UTC),
        judgment_section=JudgmentSection(
            [{"thesis": "主权资本正成为长期买家", "marker": "新证据"}]
        ),
    )
    assert "主权资本" not in html and "新证据" not in html


def test_real_english_source_translation_and_new_url_republication(tmp_path):
    original = "Microsoft (NasdaqGS:MSFT) plans to invest over US$10b in cloud and AI infrastructure across Gulf countries by 2030."
    translated = "Microsoft (NasdaqGS:MSFT) 计划到 2030 年在海湾国家投资超过 100 亿美元用于云和 AI 基础设施。"
    from src.processors.news_presentation import publication_text

    obj = source(
        publication_text(translated),
        original_title=original,
        excerpt=original,
        validated_text=translated,
        presentation_version=1,
        mode="checked_translation",
    )
    section = build(obj)
    assert section and section.items[0]["fact"] == publication_text(translated)
    assert "NasdaqGS" not in section.items[0]["fact"]
    commit_publications(section, tmp_path, today=TODAY)
    obj.evidence[0]["url"] = "https://example.com/syndicated"
    obj.footnotes[0].url = obj.evidence[0]["url"]
    assert build(obj, history=load_publications(tmp_path)) is None
    for bad in [
        translated.replace("100", "1000"),
        translated.replace("计划", "已经"),
        translated.replace("Microsoft", "主权资本"),
    ]:
        obj.evidence[0]["validated_text"] = bad
        obj.evidence[0]["output_text"] = publication_text(bad)
        obj.summary_html = publication_text(bad)
        assert build(obj) is None


def test_hidden_voice_block_does_not_trigger_judgment():
    from datetime import UTC, datetime

    obj = source()
    point = SimpleNamespace(
        text=FACT, source_url=obj.evidence[0]["url"], evidence=obj.evidence, footnote_index=1
    )
    section = build_judgment_section(sources={"voices": [point]}, today=TODAY)
    assert section
    html = render_email(
        signals=[],
        generated_at=datetime(2026, 9, 26, tzinfo=UTC),
        figures=[],
        figure_summaries=[SimpleNamespace(person="纳德拉", items=[point])],
        judgment_section=section,
    )
    assert "后续验证" not in html


def test_frontier_third_unpublished_item_is_not_a_source():
    from src.processors.thesis.renderer import publication_sources

    obj = source()
    point = SimpleNamespace(text=FACT, source_url=obj.evidence[0]["url"], evidence=obj.evidence)
    inputs = publication_sources(frontier_labs_events=[SimpleNamespace(), SimpleNamespace(), point])
    assert build_judgment_section(sources=inputs, today=TODAY) is None


def test_empty_selection_has_auditable_reason():
    audit = {}
    assert (
        build_judgment_section(
            sources=sources(source("微软股价今天上涨10%。")), today=TODAY, selection_audit=audit
        )
        is None
    )
    assert audit["published"] == 0
    assert audit["decisions"][0]["reason"] == "no_bounded_long_term_watchpoint"


def test_invalid_older_copy_does_not_shadow_valid_source():
    stale = source(published="2020-09-26")
    valid = source()
    result = build(stale, valid)
    assert result and len(result.items) == 1
    assert result.items[0]["source_date"] == "2026-09-26"
