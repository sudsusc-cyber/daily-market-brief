"""Cross-entity/category regressions. All examples are synthetic, not live news."""

import json
from datetime import UTC, datetime
from itertools import product
from types import SimpleNamespace

import pytest
from bs4 import BeautifulSoup

from src.config import Holding
from src.processors import news_presentation
from src.processors.macro_events import edition_events, extract_event
from src.processors.macro_filter import _rebuild_safe_html
from src.processors.news_presentation import (
    present,
    publication_text,
    replay_presentation,
    voice_text,
)
from src.processors.source_grounding import grounded_text
from src.processors.thesis.extractor import _verified_grounding_row


@pytest.mark.parametrize(
    "issuer,listing,publisher",
    list(
        product(
            ["Northstar Robotics", "远山制造", "Meridian Labs"],
            ["(XNAS:NRBT)", "（证券代码：02123）", "(NRBT.AX)", "(02123.HK)", "(TSX:MRD)"],
            ["New Chronicle", "远方日报"],
        )
    ),
)
def test_listing_and_publisher_grammar_works_without_issuer_or_publisher_rules(
    issuer, listing, publisher
):
    raw = f"{issuer} {listing} 尚未批准9月30日的1,200万美元计划 - {publisher}"
    output = present(raw, source_name=publisher)
    assert output.text == f"{issuer} 尚未批准9月30日的1,200万美元计划".replace("制造 尚", "制造尚")
    assert set(output.operations) >= {"qualified_listing", "publisher_tail"}
    assert "1,200" in output.text and "尚未批准" in output.text
    assert publication_text(output.text, source_name=publisher) == output.text


def test_bare_symbols_require_configured_adjacent_issuer_and_a_name_boundary(monkeypatch):
    monkeypatch.setattr(
        news_presentation,
        "HOLDINGS",
        [Holding("NRBT", "Northstar Robotics Corporation", "example.com")],
    )
    assert (
        publication_text("Northstar Robotics (NRBT) 尚未获批。") == "Northstar Robotics 尚未获批。"
    )
    for raw in [
        "Other NorthstarRobotics (NRBT) 尚未获批。",
        "FakeNorthstar Robotics (NRBT) 尚未获批。",
        "Northstar Robotics (EPS) 增长20%。",
        "新算法（AI）未通过评估。",
        "Northstar Robotics（增长20%）尚未获批。",
        "Other Company (NRBT) 尚未获批。",
    ]:
        assert publication_text(raw) == raw


@pytest.mark.parametrize(
    "teaser", ["下面是您需要关注的要点。", "以下是投资者值得了解的内容", "下面是预期情况。"]
)
def test_read_on_grammar_only_removes_standalone_terminal_teasers(teaser):
    assert publication_text("收入增长20%。" + teaser) == "收入增长20%"
    details = "收入增长20%。下面是需要关注的内容：9月30日尚未获得批准。"
    assert publication_text(details) == details
    assert publication_text(teaser + "收入增长20%。") == teaser + "收入增长20%。"


@pytest.mark.parametrize(
    "speaker,role",
    [("Jane Quinn", "Northstar CTO "), ("张小明", "远山董事长"), ("Mina Park", "Meridian CFO ")],
)
def test_speaker_attribution_is_metadata_driven_and_keeps_fact_status(speaker, role):
    body = "9月30日尚未批准1,200万美元投资计划。"
    assert voice_text(role + speaker + "表示：" + body, speaker) == body
    for verb in ["否认", "警告", "批评", "在周二表示", "的同事表示"]:
        raw = role + speaker + verb + body
        assert voice_text(raw, speaker) == raw
    other = "他人称" + role + speaker + "表示" + body
    assert voice_text(other, speaker) == other


@pytest.mark.parametrize(
    "text,topic",
    [
        ("央行公布新的利率决定。", "货币政策"),
        ("The central bank announces a rate decision.", "货币政策"),
        ("The central bank holds rates after inflation data rises.", "货币政策"),
        ("After inflation data rises, the central bank holds rates.", "货币政策"),
        ("通胀数据升至3%，此前央行维持利率。", "通胀数据"),
        ("Inflation data rises after the central bank cuts interest rates.", "通胀数据"),
        ("The government publishes a budget after a factory lowers its spending.", "财政政策"),
        ("A factory lowers its budget after the government publishes inflation data.", "通胀数据"),
        (
            "The firm raises interest rates on auto loans while the central bank discusses inflation.",
            "信用市场",
        ),
        (
            "A company raises interest rates. The central bank publishes employment data.",
            "就业市场",
        ),
        ("Treasury yields rise after China and America discuss tariffs.", "美债市场"),
        ("China and America discuss tariffs while Treasury yields rise.", "中美关系"),
        ("Oil rebounds as Iran talks continue.", "中东局势"),
        ("Oil rebounds in the Gulf of Mexico.", "能源市场"),
        ("Canada government announces a budget.", "财政政策"),
        ("An unknown country announces a meeting.", "其他宏观"),
    ],
)
def test_event_roles_and_main_clause_override_incidental_keyword_order(text, topic):
    event = extract_event(text)
    assert event.topic == topic
    audit = json.loads(json.dumps(event.audit()))
    assert audit["version"] == 1
    assert all(text[s["start"] : s["end"]] == s["text"] for s in audit["spans"])
    if event.action:
        assert event.action in text
    if event.actor:
        assert event.actor in text


@pytest.mark.parametrize("country", ["Canada", "Chile", "Norway", "泰国", "新西兰"])
@pytest.mark.parametrize("state", ["批准", "尚未批准", "计划批准"])
def test_fiscal_category_generalizes_without_country_or_state_exceptions(country, state):
    text = f"{country}政府{state}9月30日的预算，金额为1,200万美元。"
    event = extract_event(text)
    assert event.topic == "财政政策"
    assert event.time == ("9月30日",)
    if state == "尚未批准":
        assert "negative" in event.states
    if state == "计划批准":
        assert "planned" in event.states


def test_edition_context_resolution_does_not_cross_an_explicit_other_partner():
    events = edition_events(["美国与中国讨论贸易。", "中国改变谈判策略。", "中国与日本讨论经济。"])
    assert [e.topic for e in events] == ["中美关系", "中美关系", "中国经济"]
    assert events[1].decision == "edition_bilateral_followup"


def source(title, i=0):
    return SimpleNamespace(
        title=title,
        summary="",
        url=f"https://example.com/{i}",
        source="New Chronicle",
        published_at=datetime(2026, 9, 30, tzinfo=UTC),
    )


def test_grouping_preserves_changed_numbers_states_dates_and_each_source():
    titles = [
        "甲国政府批准9月29日的预算，金额为100万美元。",
        "乙国政府尚未批准9月30日的预算，金额为200万美元。",
        "甲国政府计划批准10月1日的预算，金额为300万美元。",
    ]
    items = [source(t, i) for i, t in enumerate(titles)]
    evidence = []
    html, notes = _rebuild_safe_html("<p>虚构标题。编造事实[3][1][2]</p>", items, evidence)
    soup = BeautifulSoup(html, "html.parser")
    assert len(soup.select("p")) == 1
    assert soup.select_one("[data-macro-heading]").text == "财政政策。"
    assert all(t in soup.text for t in titles)
    assert {n.url for n in notes} == {i.url for i in items}
    assert len(evidence) == 3
    for row in evidence:
        assert row["macro_event"]["text"] == row["output_text"]
        assert row["macro_topic"] == row["macro_event"]["topic"]
        assert row["presentation_version"] == 3
        assert _verified_grounding_row(SimpleNamespace(summary_html=html), row)
    assert "编造" not in html


def test_original_source_and_versioned_replay_survive_new_cleanup():
    raw = "远山制造（02123.HK）尚未批准1,200万美元计划 - New Chronicle"
    item = source(raw)
    output, rows = grounded_text(raw, [item])
    row = rows[0]
    assert item.title == row["original_title"] == row["excerpt"] == raw
    assert row["output_text"] == output != raw
    assert row["presentation_version"] == 3
    assert "qualified_listing" in row["presentation_operations"]
    obj = SimpleNamespace(summary_html=output)
    assert _verified_grounding_row(obj, row)
    assert not _verified_grounding_row(
        obj, dict(row, output_text=output.replace("尚未批准", "批准"))
    )
    # Unknown exchange grammar is new in v3. v1/v2 replay must remain frozen.
    historical = "远山制造 (XNAS:NRBT) 尚未批准投资计划。"
    for version in [1, 2]:
        assert replay_presentation(historical, {"presentation_version": version}) == historical
    assert "(XNAS:" not in replay_presentation(historical, {"presentation_version": 3})


@pytest.mark.parametrize(
    "text",
    [
        "The taxicab company announces a new trademark.",
        "The bank discusses technology talent travel.",
    ],
)
def test_domain_words_are_not_substrings_of_unrelated_words(text):
    event = extract_event(text)
    assert event.topic == "其他宏观"


def test_historical_voice_mapping_is_verified_under_its_original_contract():
    text = "Nvidia CEO Jensen Huang 称 AI 模型蒸馏仍有技术风险。"
    _, rows = grounded_text(text, [source(text)])
    row = dict(rows[0], presentation_version=2, presentation_speaker="黄仁勋")
    row["output_text"] = replay_presentation(text, row)
    assert row["output_text"] == "AI 模型蒸馏仍有技术风险。"
    assert _verified_grounding_row(SimpleNamespace(text=row["output_text"]), row)
    assert not _verified_grounding_row(
        SimpleNamespace(text=row["output_text"]), dict(row, presentation_version=1)
    )


@pytest.mark.parametrize(
    "name,ticker", [("苹果", "AAPL"), ("微软", "MSFT"), ("万事达", "MA"), ("英伟达", "NVDA")]
)
def test_existing_chinese_display_metadata_uses_the_same_listing_grammar(name, ticker):
    assert publication_text(f"{name}（{ticker}）尚未批准计划。") == f"{name}尚未批准计划。"



def test_currency_and_quantity_cannot_become_country_or_observation_date():
    event = extract_event("China discusses an economic plan costing 2000 US dollars.")
    assert event.topic == "中国经济"
    assert "us" not in event.geography
    assert event.time == ()
