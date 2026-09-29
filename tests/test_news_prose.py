"""Consistent news punctuation and citation spacing without changing source data."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from bs4 import BeautifulSoup

from src.processors.html_safe import FOOTNOTE_ANCHOR_STYLE, safe_anchor
from src.renderer.news_prose import news_paragraphs, sentence_end
from src.renderer.render import render_email


@pytest.mark.parametrize(
    "text,expected",
    [
        ("营收增长10%", "营收增长10%。"),
        ("营收增长10%。", "营收增长10%。"),
        ("营收增长10%.", "营收增长10%。"),
        ("营收增长10%；", "营收增长10%。"),
        ("交易量为1,000.25亿美元", "交易量为1,000.25亿美元。"),
        ("是否已批准?", "是否已批准？"),
        ("尚未批准!", "尚未批准！"),
        ("他说“尚未批准”", "他说“尚未批准。”"),
        ("他说“尚未批准。”", "他说“尚未批准。”"),
        ("尚未批准（2026）", "尚未批准（2026）。"),
        ("记录 [2026]", "记录 [2026]。"),
        ("留待观察…", "留待观察…"),
        ("", ""),
    ],
)
def test_sentence_end_preserves_quantities_states_and_quote_structure(text, expected):
    assert sentence_end(text) == expected
    assert sentence_end(expected) == expected


def test_html_endings_precede_citations_without_modifying_urls_or_text():
    url = "https://example.com/a?amount=1.25&done=false"
    citation = "<sup>" + safe_anchor(url, "[1]", style=FOOTNOTE_ANCHOR_STYLE) + "</sup>"
    body = (
        "<div><div><span>微软</span>│尚未批准"
        + citation
        + "</div><p>收入增长10%。；现金流减少5%"
        + citation
        + "</p></div>"
    )
    output = news_paragraphs(body)
    soup = BeautifulSoup(output, "html.parser")
    assert "尚未批准。" in soup.get_text()
    assert "现金流减少5%。" in soup.get_text()
    assert "。；" not in output
    assert [a["href"] for a in soup.select("a")] == [url, url]
    assert all(
        "margin-left:3px" in a["style"] and "margin-right:2px" in a["style"]
        for a in soup.select("a")
    )
    assert news_paragraphs(output) == output
    assert "尚未批准</" not in output


def test_entities_cannot_turn_into_active_html_during_formatting():
    output = news_paragraphs(
        '<p>文本 &lt;script&gt;alert(1)&lt;/script&gt;<sup><a href="https://example.com">[1]</a></sup></p>'
    )
    assert "<script>" not in output
    assert "&lt;script&gt;" in output


def test_every_news_surface_uses_punctuation_and_source_separators_after_minification():
    notes = [
        SimpleNamespace(index=i, url=f"https://example.com/{i}", source=f"来源{i}") for i in (1, 2)
    ]
    summary = SimpleNamespace(
        summary_html='<p>尚未批准<sup><a href="https://example.com/1">[1]</a></sup></p>',
        footnotes=notes,
    )
    voice = SimpleNamespace(
        person="纳德拉",
        items=[SimpleNamespace(text="仍需等待", footnote_index=1, source_url=notes[0].url)],
    )
    frontier = [
        SimpleNamespace(lab="OpenAI", text="尚未发布", source_url=n.url, source_name=n.source)
        for n in notes
    ]
    html = render_email(
        signals=[],
        generated_at=datetime(2026, 9, 26, tzinfo=UTC),
        company_news_summary=summary,
        macro_news_summary=summary,
        figures=[SimpleNamespace()],
        figure_summaries=[voice],
        figure_footnotes=notes,
        frontier_labs_items=frontier,
    )
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    assert "尚未批准。" in text and "仍需等待。" in text and "尚未发布。" in text
    groups = soup.select('tr[data-source-list="true"]')
    assert len(groups) == 4
    for group in groups:
        assert "·" not in group.get_text()
        assert len(group.select("a.source-link")) == 2
        for a in group.select("a.source-link"):
            # Shared styles can be factored into CSS; either form must retain separation.
            assert "display:inline-block" in a.get("style", "") or "display:inline-block" in html
    assert summary.summary_html.startswith(
        "<p>尚未批准<sup>"
    )  # presentation never mutates evidence
