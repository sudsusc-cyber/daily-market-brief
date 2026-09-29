"""Regressions from formal edition 36527674622; no live mail or LLM."""
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from bs4 import BeautifulSoup

from src.processors.macro_filter import MacroNewsSummary, _rebuild_safe_html
from src.processors.macro_topics import macro_topic
from src.processors.news_presentation import publication_text
from src.processors.thesis.extractor import _verified_grounding_row
from src.processors.translation_guard import translation_errors
from src.processors.translator import translate_titles
from src.renderer.render import render_email

NOW = datetime(2026, 9, 29, tzinfo=UTC)
ROWS = [
    ('Oil price and US Treasury yields in tightest relationship since 1990',
     '油价与美国国债收益率的关联达到 1990 年以来最紧密水平'),
    ('Traditionally Terrible October Looms for Beleaguered Treasuries',
     '承压的美国国债即将迎来历来表现不佳的十月'),
    ('Germany issues EU budget ultimatum', 'Germany 就 EU 预算发出最后通牒'),
]


def test_actual_edition_has_independent_fiscal_heading_and_bound_chinese_names():
    items = [SimpleNamespace(title=title, summary='', source='Source',
                             source_excerpt=title, translated_excerpt=translation,
                             published_at=NOW, url=f'https://example.com/{i}')
             for i, (title, translation) in enumerate(ROWS)]
    evidence = []
    html, notes = _rebuild_safe_html('<p>美债市场。无依据改写[1][2][3]</p>', items, evidence)
    paragraphs = BeautifulSoup(html, 'html.parser').select('p')
    assert [p.select_one('[data-macro-heading]').text for p in paragraphs] == ['美债市场。', '财政政策。']
    assert len(paragraphs[0].select('[data-macro-fact]')) == 2
    assert paragraphs[1].get_text().startswith('财政政策。德国就欧盟预算发出最后通牒。')
    assert [a['href'] for a in paragraphs[1].select('a')] == [items[2].url]
    assert evidence[2]['original_title'] == ROWS[2][0]
    assert evidence[2]['validated_text'] == ROWS[2][1]
    assert all(_verified_grounding_row(SimpleNamespace(summary_html=r['output_text']), r) for r in evidence)
    summary = MacroNewsSummary(summary_html=html, footnotes=notes, evidence=evidence)
    rendered = BeautifulSoup(render_email(signals=[], generated_at=NOW, macro_news_summary=summary), 'html.parser')
    assert len(rendered.select('[data-macro-heading]')) == 2
    assert 'Germany' not in rendered.get_text() and 'EU 预算' not in rendered.get_text()


@pytest.mark.parametrize('text', ['Germany issues EU budget ultimatum', '德国就欧盟预算发出最后通牒',
                                 'Federal budget talks continue', '政府预算谈判继续'])
def test_public_budget_classifies_as_fiscal(text):
    assert macro_topic(text) == '财政政策'


def test_corporate_budget_does_not_become_government_policy():
    assert macro_topic('Microsoft sets Germany factory budget') != '财政政策'
    assert macro_topic('微软讨论德国工厂预算') != '财政政策'


def test_unclassified_paragraphs_have_neutral_headings_without_false_merging():
    items = [SimpleNamespace(title=t, summary='', source='Source', url=f'https://example.com/{i}')
             for i, t in enumerate(['甲国公布统计结果。', '乙国宣布会议日程。'])]
    html, _ = _rebuild_safe_html('<p>全球危机。[1][2]</p>', items)
    paragraphs = BeautifulSoup(html, 'html.parser').select('p')
    assert len(paragraphs) == 2
    assert all(p.select_one('[data-macro-heading]').text == '宏观观察。' for p in paragraphs)
    assert all(len(p.select('[data-macro-fact]')) == 1 for p in paragraphs)


def test_country_display_preserves_publisher_proper_names_and_word_boundaries():
    assert publication_text('Germany 就 EU 预算发出最后通牒') == '德国就欧盟预算发出最后通牒'
    assert publication_text('EURO Germany Today 报道', source_name='Germany Today') == 'EURO Germany Today 报道'


def test_literal_calendar_translation_retries_without_inventing_transition():
    original = ROWS[1][0]
    bad = '传统上表现糟糕的十月逼近处境艰难的 Treasuries'
    good = ROWS[1][1]
    assert 'calendar_market_word_order' in translation_errors(original, bad)
    assert translation_errors(original, good) == []
    replies = iter([bad, good])
    prompts = []
    def chat(prompt, **kwargs):
        prompts.append(prompt)
        return SimpleNamespace(text='▦ 1: ' + next(replies), error=None)
    assert translate_titles([original], client=SimpleNamespace(chat=chat)) == [good]
    assert len(prompts) == 2 and 'calendar_market_word_order' in prompts[1]


@pytest.mark.parametrize('translated', ['历来表现不佳的十月临近，美国国债仍承压',
                                       '美国国债承压，面临历来表现不佳的十月'])
def test_looming_calendar_accepts_natural_imminence_without_requiring_one_word(translated):
    assert translation_errors(ROWS[1][0], translated) == []


def test_looming_outlook_cannot_become_completed_month_performance():
    assert 'modality' in translation_errors(ROWS[1][0], '美国国债十月表现糟糕')
