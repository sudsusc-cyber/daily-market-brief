"""Source-bound recovery and category regressions; no real LLM/SMTP calls.

Two headline examples were re-fetched after run 36654114423, not recovered from
its original candidate archive. Their timestamps/order and the IPO rejection log
support reproduction but do not prove the historical translation text.
"""

import copy
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from src.collectors.figures import FigureBundle, FigureMention
from src.processors.editorial_evidence import model_status_claim, status_has_scope
from src.processors.figure_filter import filter_all, filter_one
from src.processors.source_grounding import grounded_text, publication_diagnostic
from src.processors.thesis.extractor import _verified_grounding_row
from src.processors.translation_guard import translation_errors
from src.processors.translator import translate_in_place_news, translate_titles
from src.utils.quality_details import quality_details


class Client:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def chat(self, text, **kwargs):
        self.calls.append((text, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return SimpleNamespace(text=response, error="timeout" if response is None else None)


def mention(title, index=1):
    return FigureMention(
        title, "", datetime(2026, 9, 29, tzinfo=UTC), f"https://example.com/{index}", "Source"
    )


def bundle(items, name="Sam Altman"):
    return FigureBundle(name, name, name, items)


@pytest.mark.parametrize("acronym", ["IPO", "GPU", "TPU", "API", "ETF", "SDK", "LLM"])
def test_acronym_plural_is_grammatical_but_identity_and_count_remain_checked(acronym):
    original = f"Jane says 2 {acronym}s are available."
    good = f"Jane 表示有2个{acronym}可用。"
    assert not translation_errors(original, good)
    assert not translation_errors(f"Jane says {acronym}s are useful.", f"Jane 表示{acronym}有用。")
    assert "quantities_or_units" in translation_errors(original, good.replace("2", "3"))
    assert any(
        e.startswith(("identifier:", "entity:"))
        for e in translation_errors(original, good.replace(acronym, "CPU"))
    )


def test_capital_s_and_versioned_model_identifiers_are_not_plural_suffixes():
    assert translation_errors("Jane says AWS is available.", "Jane 表示 AW 可用。")
    assert translation_errors("Jane says GPT-6.1 is available.", "Jane 表示 GPT-6.2 可用。")
    assert translation_errors("Jane says GPU is available.", "Jane 表示 GPUS 可用。")
    assert translation_errors("Jane says Claude-3-Sonnet is available.", "Jane 表示 Claude-3-Opus 可用。")


@pytest.mark.parametrize("issuer", ["OpenAI", "Anthropic", "Northstar AI"])
@pytest.mark.parametrize(
    "obj", ["IPO", "financing", "budget", "conference", "factory construction", "contract"]
)
def test_corporate_events_do_not_become_model_shutdowns(issuer, obj):
    for text in [
        f"{issuer} delayed its {obj}.",
        f"{issuer} {obj} was delayed because of model safety concerns.",
    ]:
        assert not model_status_claim(text)
        assert status_has_scope(
            SimpleNamespace(title=text, summary="", url="https://example.com/report"), text
        )


@pytest.mark.parametrize(
    "text",
    [
        "Acme AI paused model training.",
        "OpenAI postponed the launch of its new model.",
        "Anthropic model evaluation was delayed.",
        "OpenAI pauses GPT-6.1 Astra",
        "某公司暂停模型训练。",
        "模型服务已取消。",
        "OpenAI postpones model training and cancels its IPO.",
        "Acme AI cancels its IPO and its new model.",
        "某公司取消上市及新模型部署。",
    ],
)
def test_actual_product_status_still_requires_scope_and_original_body(text):
    assert model_status_claim(text)
    assert not status_has_scope(
        SimpleNamespace(title=text, summary="", url="https://example.com/report"), text
    )


def test_real_headlines_reproduce_rejection_without_claiming_original_translation():
    sources = [
        "OpenAI IPO Delayed Until AI Safety Goals Met, Altman Says - techbuzz.ai",
        "Sam Altman says IPOs ‘ill-advised’ for AI firms amid rogue agent fears - AFR",
    ]
    translations = [
        "OpenAI 的 IPO 推迟，直到 AI 安全目标达成，Altman 表示 - techbuzz.ai",
        "Sam Altman 称，在对失控智能体的担忧中，AI 公司的 IPO 是不明智的 - AFR",
    ]
    items = [mention(t, i) for i, t in enumerate(sources)]
    items[0].source = "techbuzz.ai"
    items[1].source = "AFR"
    before = copy.deepcopy([vars(i) for i in items])
    client = Client(
        "\n".join(f"▦ {i}: {t}" for i, t in enumerate(translations, 1)),
        "▦ 1: yes | score=5 | 完整译文\n▦ 2: yes | score=4 | 完整译文",
    )
    translate_in_place_news(items, client=client)
    result = filter_one(bundle(items, "奥特曼"), client=client)
    # A valid translation is not speaker identity: the first recovered RSS
    # headline contains only a surname, with no full-name source context.
    assert len(result.items) == 1
    assert result.content_rejections == ['index=1 reason=speaker_identity_unverified']
    assert len(client.calls) == 2  # initial translation and selection; no repair needed
    assert [i.title for i in items] == [i["title"] for i in before]
    for point in result.items:
        assert _verified_grounding_row(point, point.evidence[0])
    assert translation_errors(sources[0], "OpenAI 的 IPO 已上市，Altman 表示 - techbuzz.ai")
    assert "condition:until" in translation_errors(
        sources[0], "OpenAI 的 IPO 推迟，Altman 表示 - techbuzz.ai"
    )


def test_selected_translation_recovers_once_and_preserves_failed_observation():
    item = mention("Sam Altman says GPUs are useful.")
    item.source_excerpt = item.title
    item.translated_excerpt = item.title
    item.translation_diagnostic = {
        "errors": ["identifier:GPU"],
        "candidate": "Sam Altman 表示 GPU 有用。",
    }
    raw = copy.deepcopy((item.title, item.snippet, item.url, item.published_at))
    client = Client("▦ 1: yes | score=5 | 有用的硬件。", "▦ 1: Sam Altman 表示 GPU 有用。")
    result = filter_one(bundle([item]), client=client)
    assert len(result.items) == 1 and not result.content_rejections
    assert len(client.calls) == 2
    assert "identifier:GPU" in client.calls[1][0]
    assert client.calls[1][1]["timeout"] == 20
    assert (item.title, item.snippet, item.url, item.published_at) == raw
    assert result.verification_audit[0]["rejected_indexes"] == {1: "translation_unavailable"}
    repair = result.verification_audit[1]
    assert repair["before"][0]["errors"] and repair["after"][0]["errors"] == []
    assert repair["recovered"] == 1
    assert _verified_grounding_row(result.items[0], result.items[0].evidence[0])


@pytest.mark.parametrize(
    "response",
    [
        None,
        RuntimeError("timeout api_key=private-token"),
        "▦ 1: Sam Altman 表示已获批准，金额1000亿美元。",
    ],
)
def test_repair_cannot_authorize_unsupported_facts_or_erase_first_rejection(response):
    item = mention("Sam Altman says GPUs are useful.")
    client = Client("▦ 1: yes | score=5 | 没有依据的摘要", response)
    result = filter_one(bundle([item]), client=client)
    assert not result.items and result.content_rejections and not result.processing_error
    assert len(client.calls) == 3  # selection plus the two shared recovery calls
    assert result.verification_audit[0]["rejected_indexes"]
    assert "private-token" not in json.dumps(result.verification_audit)


def test_recovery_budget_is_shared_and_editorial_no_does_not_trigger_repair():
    items = [
        bundle([mention("Sam Altman says GPUs are useful.", i)], f"Person {i}") for i in range(4)
    ]
    yes = "▦ 1: yes | score=5 | 未核实摘要"
    client = Client(yes, None, None, yes, yes, "▦ 1: no | score=1 | 没有重要的新发言")
    results = filter_all(items, client=client)
    assert len(client.calls) == 6  # four selections, only two recovery batches
    assert all(not x.items for x in results)
    assert results[-1].error is None
    assert results[-1].verification_audit[0]["decisions"][0]["verdict"] == "no"


def test_unpublishable_source_is_not_retranslated_and_audit_is_bounded_redacted():
    item = mention("OpenAI cancels its new model.")
    item.snippet = "contact me@example.com api_key=secret-token " + ("x" * 3000)
    client = Client("▦ 1: yes | score=5 | 未核实摘要")
    # Rule layer regards this as company speech, so inspect the source gate directly.
    diagnostic = publication_diagnostic(item)
    assert "no_publishable_source_excerpt" in diagnostic["errors"]
    assert diagnostic["truncated"]
    serialized = json.dumps(diagnostic)
    assert "secret-token" not in serialized and "me@example.com" not in serialized
    assert len(diagnostic["snippet"]) <= 1800
    assert grounded_text("OpenAI 取消其新模型。", [item]) == ("", [])
    item.title = "Sam Altman says OpenAI cancels its new model."
    result = filter_one(bundle([item]), client=client)
    assert not result.items and result.content_rejections
    assert len(client.calls) == 1
    assert result.failure_kind == "evidence" and result.translation_failure_count == 0


@pytest.mark.parametrize("original,translated", [
    ("李明 says GPUs are useful.", "李明表示 GPU 有用。"),
    ("Jane says GPUs are useful - 科技", "Jane 表示 GPU 有用 - 科技"),
    ("王明 says APIs are useful.", "王明表示 API 有用。"),
])
def test_chinese_name_or_publisher_does_not_skip_english_translation(original, translated):
    client = Client(f"▦ 1: {translated}")
    assert translate_titles([original], client=client) == [translated]
    assert len(client.calls) == 1
    assert original in client.calls[0][0]


def test_existing_chinese_with_english_names_needs_no_translation():
    original = "Jane 表示 GPU 的实际需求仍取决于客户的部署进度。"
    client = Client()
    assert translate_titles([original], client=client) == [original]
    assert not client.calls


def test_english_selection_is_translated_after_selection_without_chinese_source():
    original = "Sam Altman says GPUs are useful."
    item = mention(original)
    client = Client(f"▦ 1: yes | score=5 | {original}", "▦ 1: Sam Altman 表示 GPU 有用。")
    result = filter_one(bundle([item]), client=client)
    assert result.items and not result.error and result.failure_kind == ""
    assert result.items[0].text == "GPU 有用。"
    assert _verified_grounding_row(result.items[0], result.items[0].evidence[0])
    assert item.title == original
    assert result.items[0].evidence[0]["excerpt"] == original
    assert len(client.calls) == 2
    selection_prompt, options = client.calls[0]
    assert "待翻译；请按原文选稿" in selection_prompt
    assert "英文不可直接刊出" not in selection_prompt + options["task_extra"]
    assert "英文原文同样可以选为 yes" in options["task_extra"]


def test_translation_outage_is_reported_as_processing_not_bad_source_evidence():
    item = mention("Sam Altman says GPUs are useful.")
    client = Client("▦ 1: yes | score=5 | Sam Altman says GPUs are useful.", None)
    result = filter_one(bundle([item]), client=client)
    assert result.failure_kind == "translation" and result.translation_failure_count == 1
    assert result.verification_audit[0]["rejected_indexes"] == {1: "translation_unavailable"}
    details = quality_details({"section_health": {"figures": {
        "translation_failures": result.translation_failure_count, "content_rejections": 0,
    }}})
    assert details == ["加工状态·人物观点：翻译处理失败 1"]
