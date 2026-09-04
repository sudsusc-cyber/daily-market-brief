from datetime import date
from types import SimpleNamespace

import pytest

from src.processors.editorial_history import EditorialHistory, similar
from src.processors.figure_filter import FigureKeyPoint, FigureSummary, assign_footnotes
from src.processors.news_summarizer import CompanyNewsSummary, Footnote

TEXT = "微软表示将持续投资人工智能基础设施建设以满足企业云计算需求"


@pytest.mark.parametrize("other", [TEXT, TEXT + "。", TEXT.replace("表示", "强调"), TEXT + "[2]"])
def test_near_rephrases(other):
    assert similar(TEXT, other)


@pytest.mark.parametrize("a,b", [
    (TEXT + "投资100亿美元", TEXT + "投资200亿美元"),
    (TEXT + "投资1.5亿美元", TEXT + "投资15亿美元"),
    (TEXT + "计划收购", TEXT + "完成收购"),
    (TEXT + "批准交易", TEXT + "拒绝交易"),
    (TEXT, "苹果正式发布全新手机并上调本季度收入指引"),
])
def test_new_facts_not_blindly_removed(a, b):
    assert not similar(a, b)


def test_history_is_committed_only_explicitly_and_expires(tmp_path):
    path = tmp_path / "history.json"
    history = EditorialHistory(path, date(2026, 9, 4))
    history.remember("company", "微软", TEXT)
    assert not path.exists()
    assert history.duplicate("company", "微软", TEXT)
    assert not history.duplicate("company", "苹果", TEXT)
    assert not history.duplicate("figures", "微软", TEXT)
    history.commit()
    assert EditorialHistory(path, date(2026, 9, 5)).duplicate("company", "微软", TEXT)
    assert not EditorialHistory(path, date(2026, 10, 5)).rows


def test_bad_state_and_bounded_context(tmp_path):
    path = tmp_path / "history.json"
    path.write_text('{broken', encoding="utf-8")
    history = EditorialHistory(path, date(2026, 9, 4))
    assert not history.rows
    for i in range(100):
        history.remember("company", "微软", TEXT + str(i))
    assert len(history.context("company")) < 6500
    assert history.context("figures") == ""


def test_company_filter_preserves_remaining_footnotes_and_html(tmp_path):
    history = EditorialHistory(tmp_path / "h.json", date(2026, 9, 4))
    history.remember("company", "微软", "微软│" + TEXT)
    kept = '<div><span>苹果</span>新产品销量100万<sup><a href="https://a.com">[2]</a></sup></div>'
    summary = CompanyNewsSummary(
        '<div><span>微软</span>│' + TEXT + '<sup><a href="https://m.com">[1]</a></sup></div>' + kept,
        [Footnote(1, "https://m.com", "媒体甲"), Footnote(2, "https://a.com", "媒体乙")],
    )
    result = history.filter_company(summary)
    assert "微软" not in result.summary_html
    assert "苹果" in result.summary_html
    assert [f.index for f in result.footnotes] == [2]
    assert not result.is_silence
    history.capture(result, [])
    assert history.duplicate("company", "苹果", "苹果新产品销量100万")


def test_all_duplicate_company_becomes_silence(tmp_path):
    history = EditorialHistory(tmp_path / "h.json", date(2026, 9, 4))
    history.remember("company", "微软", "微软" + TEXT)
    summary = CompanyNewsSummary('<div><span>微软</span>' + TEXT + '</div>')
    assert history.filter_company(summary).is_silence


def test_figures_filter_before_footnote_numbering(tmp_path):
    history = EditorialHistory(tmp_path / "h.json", date(2026, 9, 4))
    history.remember("figures", "纳德拉", TEXT)
    summary = FigureSummary("纳德拉", items=[
        FigureKeyPoint(TEXT.replace("表示", "强调"), "https://old.com", "旧来源"),
        FigureKeyPoint("正式上调本季度企业订单增长预测至20%", "https://new.com", "新来源"),
    ])
    history.filter_figure(summary)
    notes = assign_footnotes([summary])
    assert len(notes) == 1
    assert notes[0].url == "https://new.com"
    assert summary.items[0].footnote_index == 1
    history.capture(None, [summary])
    assert history.duplicate("figures", "纳德拉", summary.items[0].text)


def test_summarizer_receives_history_and_filters_output(tmp_path):
    from src.processors import news_summarizer
    history = EditorialHistory(tmp_path / "h.json", date(2026, 9, 4))
    history.remember("company", "微软", "微软│" + TEXT)
    calls = []

    def chat(*args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(text=f"<strong>微软</strong>——{TEXT}[1]", error=None)

    bundle = SimpleNamespace(holding=SimpleNamespace(ticker="MSFT", name="微软"), error=None, items=[SimpleNamespace(
        title="Microsoft AI", title_zh="微软AI", summary="", url="https://m.com", source="Reuters",
    )])
    result = news_summarizer.summarize([bundle], client=SimpleNamespace(chat=chat), history=history)
    assert result.is_silence
    assert len(calls) == 1
    assert TEXT in calls[0]["task_extra"]


def test_figure_generation_receives_history_without_extra_call(tmp_path):
    from src.processors import figure_filter
    history = EditorialHistory(tmp_path / "h.json", date(2026, 9, 4))
    history.remember("figures", "纳德拉", TEXT)
    calls = []

    def chat(*args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(text=f"▦ 1: yes | score=5 | {TEXT}", error=None)

    bundle = SimpleNamespace(person="纳德拉", person_en="Satya Nadella", error=None, items=[
        SimpleNamespace(title="Nadella said AI investment will continue", snippet="",
                        source="Reuters", url="https://m.com", published_at=None),
    ])
    result = figure_filter.filter_all([bundle], client=SimpleNamespace(chat=chat), history=history)
    assert not result[0].items
    assert len(calls) == 1
    assert TEXT in calls[0]["task_extra"]


def test_main_commits_editorial_history_after_smtp_acceptance():
    import inspect

    from src import main
    code = inspect.getsource(main)
    assert code.index("delivery = send_html_email(") < code.index("editorial_history.capture(")
    assert code.index("editorial_history.capture(") < code.index("editorial_history.commit()")


def test_malformed_future_and_expired_rows_are_ignored(tmp_path):
    import json
    path = tmp_path / "h.json"
    path.write_text(json.dumps([
        {"date": "bad"},
        {"date": "2026-09-05", "section": "company", "entity": "微软", "text": TEXT},
        {"date": "2026-07-01", "section": "company", "entity": "微软", "text": TEXT},
        {"date": "2026-09-03", "section": "company", "entity": "微软", "text": TEXT},
    ]), encoding="utf-8")
    history = EditorialHistory(path, date(2026, 9, 4))
    assert len(history.rows) == 1
