"""Regressions from the 2026-09-30 archived formal email and pipeline audit."""

from types import SimpleNamespace

import pytest
from bs4 import BeautifulSoup

from src.processors.macro_filter import _rebuild_safe_html
from src.processors.news_presentation import present, replay_presentation
from src.processors.news_selection import factual_excerpt, frontier_candidate
from src.processors.source_grounding import grounded_text
from src.utils.brief_audit import content_report


def article(title, *, summary="", translated="", number=1):
    return SimpleNamespace(title=title, summary=summary, source="Source",
                           url=f"https://example.com/{number}",
                           source_excerpt=title, translated_excerpt=translated)


@pytest.mark.parametrize("issuer", ["Anthropic", "Acme", "远山科技"])
def test_vague_frontier_teaser_cannot_become_a_news_fact(issuer):
    title = f"The Scary Part About {issuer}'s IPO Doc That Gets Little Attention"
    row = article(title, summary=f"<a href='https://example.com'>{title}</a> Source")
    assert factual_excerpt(row) == ""
    assert not frontier_candidate(row)
    row.translated_excerpt = f"{issuer} IPO 文件中少有人注意的可怕部分"
    assert grounded_text(row.translated_excerpt, [row]) == ("", [])


def test_teaser_with_a_concrete_body_fact_keeps_the_fact():
    row = article("The Scary Part About Acme's IPO Doc That Gets Little Attention",
                  summary="Acme reported annual revenue of $10 billion and filed for an IPO.")
    assert factual_excerpt(row) == row.summary


def test_specific_fact_in_a_teaser_shaped_title_is_not_discarded():
    title = "The scary part about Acme's IPO is that revenue fell 20%."
    assert factual_excerpt(article(title)) == title
    assert factual_excerpt(article("IPO 文件中少有人注意的可怕部分")) == ""


def test_actual_yield_record_rephrasing_shares_one_fact_and_two_sources():
    rows = [
        article("US 30-year Treasury yield hits highest since 2002",
                translated="美国 30 年期国债收益率触及 2002 年以来最高", number=1),
        article("30-year Treasury bond yield scales to highest level since 2002",
                translated="30 年期美国国债收益率攀升至 2002 年以来最高水平", number=2),
    ]
    evidence = []
    html, footnotes = _rebuild_safe_html("<p>美债市场。[1][2]</p>", rows, evidence)
    soup = BeautifulSoup(html, "html.parser")
    assert len(soup.select("[data-macro-fact]")) == 1
    assert len(footnotes) == len(evidence) == 2
    assert {note.url for note in footnotes} == {row.url for row in rows}


@pytest.mark.parametrize("different", [
    "美国10年期国债收益率触及2002年以来最高。",
    "美国30年期国债收益率触及2003年以来最高。",
    "日本30年期国债收益率触及2002年以来最高。",
    "美国30年期国债收益率触及2002年以来最低。",
    "美国30年期国债收益率可能触及2002年以来最高。",
    "美国30年期国债收益率未触及2002年以来最高。",
    "美国30年期国债收益率触及5%，创2002年以来最高。",
])
def test_yield_fact_normalization_does_not_erase_updates_or_uncertainty(different):
    assert present(different).text != present("美国30年期国债收益率触及2002年以来最高。").text


def test_presentation_v4_replays_without_new_yield_normalization():
    source = "美国30年期国债收益率触及2002年以来最高。"
    assert replay_presentation(source, {"presentation_version": 4}) == source
    assert present(source).text == "美国30年期国债收益率升至2002年以来最高。"


def test_checked_source_extract_is_a_disclosed_format_not_a_quality_failure():
    evidence = [{"url": "https://example.com/1", "mode": "checked_translation",
                 "publication_path": "source_fallback", "output_text": "公司公布营收增长。"}]
    report = content_report(signals=[], valuations={}, sentiment=None,
                            expected_tickers=[], news={"company": (1, [SimpleNamespace(evidence=evidence)])})
    assert report["news_coverage"]["company"]["extractive_fallbacks"] == 1
    assert report["status"] == "verified"
    report = content_report(signals=[], valuations={}, sentiment=None,
                            expected_tickers=[], news={"company": (1, [SimpleNamespace(evidence=evidence)])},
                            section_health={"company": {"content_rejections": 1}})
    assert report["status"] == "degraded"
