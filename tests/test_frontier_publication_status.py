"""A quiet news day, source outage and rejected evidence are different outcomes."""
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.collectors.frontier_labs import FrontierBundle, FrontierItem
from src.processors import frontier_labs_filter as frontier
from src.renderer.render import render_email
from src.utils.brief_audit import content_report

NOW = datetime(2026, 9, 29, tzinfo=UTC)
FACT = "OpenAI 宣布投资100亿美元建设数据中心。"


def item(title=FACT, *, lab="OpenAI", suffix="good"):
    return FrontierItem(lab, title, "", NOW, f"https://example.com/{suffix}", "Reuters",
                        "google_news", ["MSFT", "NVDA"])


def bundle(items=(), *, errors=(), lab="OpenAI"):
    return FrontierBundle(lab, ["MSFT", "NVDA"], list(items), list(errors))


def client(text=None, *, responses=None):
    mock = Mock()
    mock.chat.return_value = SimpleNamespace(text=text, error="timeout" if text is None else None)
    if responses is not None:
        mock.chat.side_effect = [SimpleNamespace(text=t, error="timeout" if t is None else None) for t in responses]
    return mock


def yes(index=1):
    return f"▦ {index}: yes | score=5 | tickers=MSFT,NVDA | {FACT}"


def rendered(report):
    return render_email(signals=[], generated_at=NOW, frontier_labs_items=report.items,
                        frontier_labs_fallback_note=report.fallback_note)


@pytest.mark.parametrize("scenario", ["empty", "all_no", "low_score", "rule_filtered", "retry_no"])
def test_normal_silence_has_no_failure_notice_or_quality_alert(scenario):
    data = [] if scenario == "empty" else [item()]
    response = "▦ 1: no | score=2 | 普通工具更新"
    llm = client(response)
    if scenario == "low_score":
        llm = client("▦ 1: yes | score=3 | tickers=MSFT | 普通工具更新")
    if scenario == "rule_filtered":
        data = [item("Is OpenAI a good stock to buy now?")]
    if scenario == "retry_no":
        llm = client(responses=["bad format", response])
    report = frontier.filter_all_report([bundle(data)], client=llm)
    assert report.state == "silent"
    assert not report.failures and not report.fallback_note and not report.items
    assert "前沿动态" not in rendered(report)
    audit = content_report(signals=[], valuations={}, sentiment=None, expected_tickers=[],
                           news={"frontier": (len(data), report.items)}, section_health={"frontier": report.health()})
    assert audit["status"] == "verified"
    if scenario in {"empty", "rule_filtered"}:
        llm.chat.assert_not_called()


def test_all_no_is_silence_even_if_sources_have_no_chinese_translation():
    report = frontier.filter_all_report([bundle([item("OpenAI announces a minor tool update")])],
                                        client=client("▦ 1: no | score=1 | 普通工具更新"))
    assert report.state == "silent"
    assert not report.content_rejections


@pytest.mark.parametrize("items", [[], [item("Is OpenAI a good stock to buy now?")]])
def test_source_error_survives_empty_or_editorially_filtered_candidates(items):
    report = frontier.filter_all_report([bundle(items, errors=["RSS timeout"])], client=client())
    assert report.state == "source_unavailable"
    assert report.health()["processing_failures"] == 0
    assert not report.health()["silence"]
    assert "来源读取失败" in rendered(report)
    assert "整理未完成" not in rendered(report)


def test_transport_failure_remains_distinct_from_no_news():
    llm = client()
    report = frontier.filter_all_report([bundle([item()])], client=llm)
    assert llm.chat.call_count == 2
    assert report.state == "processing_failed"
    assert "筛选暂不可用" in rendered(report)
    assert len(report.processing_failures) == 1 and not report.content_rejections


def test_untranslated_candidate_is_rejected_without_poisoning_verified_neighbor():
    bad = item("OpenAI announces new cloud agreement", suffix="untranslated")
    llm = client(yes() + "\n" + yes(2))
    report = frontier.filter_all_report([bundle([item(), bad])], client=llm)
    assert llm.chat.call_count == 2  # One selection plus one bounded translation recovery.
    assert report.state == "partial"
    assert len(report.items) == 1 and report.items[0].text == FACT
    assert not report.processing_failures
    assert len(report.content_rejections) == 1
    assert "index=2 reason=no_verified_chinese_excerpt" in report.content_rejections[0]
    assert report.fallback_note is None
    assert "建设数据中心。" in rendered(report) and "暂不刊载" not in rendered(report)
    assert report.items[0].evidence[0]["excerpt"] == FACT
    audit = content_report(signals=[], valuations={}, sentiment=None, expected_tickers=[],
                           news={"frontier": (2, report.items)}, section_health={"frontier": report.health()})
    assert audit["status"] == "degraded"  # A usable row must not hide the rejection.


def test_all_rejected_is_content_validation_failure_not_silence_or_processing_failure():
    report = frontier.filter_all_report([bundle([item("OpenAI announces cloud agreement")])], client=client(yes()))
    assert report.state == "content_rejected"
    assert not report.processing_failures and not report.health()["silence"]
    assert "候选内容未通过核验" in rendered(report)
    assert "整理未完成" not in rendered(report)


def test_unsafe_url_cannot_be_reported_as_successful_silence():
    unsafe = item()
    unsafe.url = "javascript:alert(1)"
    report = frontier.filter_all_report([bundle([unsafe])], client=client(yes()))
    assert not report.items
    assert report.state == "content_rejected"
    assert "unsafe_source_url" in report.content_rejections[0]


def test_partial_protocol_failure_preserves_unambiguous_verified_rows():
    llm = client(responses=[yes(), yes()])  # Index 2 never classified.
    report = frontier.filter_all_report([bundle([item(), item(suffix="other")])], client=llm)
    assert llm.chat.call_count == 2
    assert len(report.items) == 1 and report.state == "partial"
    assert len(report.processing_failures) == 1
    assert "建设数据中心。" in rendered(report)


def test_duplicate_index_cannot_leak_a_conflicting_decision():
    data = [item(), item("OpenAI 宣布新的企业云合作。", suffix="other")]
    output = yes() + "\n▦ 1: no | score=2 | 不刊载\n" + yes(2)
    report = frontier.filter_all_report([bundle(data)], client=client(output))
    assert report.state == "partial"
    assert [p.source_url for p in report.items] == [data[1].url]
    assert report.processing_failures and "duplicates=[1]" in report.processing_failures[0]


def test_source_and_model_failures_are_both_preserved():
    report = frontier.filter_all_report([bundle([item()], errors=["one feed down"])], client=client())
    assert len(report.source_failures) == len(report.processing_failures) == 1
    assert not report.health()["silence"]


def test_archived_diagnostics_are_bounded_and_redacted(caplog):
    llm = client()
    llm.chat.return_value.error = "request failed token=secret-model-value " + "x" * 500
    report = frontier.filter_all_report([
        bundle([item()], errors=["request failed api_key=secret-source-value " + "x" * 500])
    ], client=llm)
    assert "secret-model-value" not in str(report.failures) + caplog.text
    assert "secret-source-value" not in str(report.failures)
    assert all(len(message) < 280 for message in report.failures)


def test_ambiguous_duplicate_cannot_suppress_an_independent_equivalent_source():
    data = [item(), item(suffix="equivalent")]
    output = yes() + "\n▦ 1: no | score=2 | 不刊载\n" + yes(2)
    report = frontier.filter_all_report([bundle(data)], client=client(output))
    assert [p.source_url for p in report.items] == [data[1].url]
    # A genuinely equivalent source is still deduplicated when both indexes are valid.
    clean = frontier.filter_all_report([bundle(data)], client=client(yes() + "\n" + yes(2)))
    assert len(clean.items) == 1 and not clean.failures


def test_eight_candidates_per_lab_reproduces_formal_failure_shape_without_whole_lab_loss():
    bundles, responses = [], []
    for lab in ("OpenAI", "Anthropic"):
        data = [item(f"{lab} 宣布投资{i + 1}亿美元建设数据中心。", lab=lab, suffix=f"{lab}-{i}") for i in range(8)]
        data[3].title = f"{lab} announces cloud agreement"
        data[6].title = f"{lab} announces model release"
        bundles.append(bundle(data, lab=lab))
        responses.append("\n".join(yes(i) for i in range(1, 9)))
        responses.append(None)  # Translation outage does not consume the next lab's selection.
    llm = client(responses=responses)
    report = frontier.filter_all_report(bundles, client=llm)
    assert llm.chat.call_count == 4
    assert {p.lab for p in report.items} == {"OpenAI", "Anthropic"}
    assert len(report.content_rejections) == 4 and not report.processing_failures
    assert report.state == "partial" and not report.fallback_note
    assert all(point.evidence for point in report.items)
