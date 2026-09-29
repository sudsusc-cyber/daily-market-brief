"""Regressions from formal run 36502706162: relevance, source prose and themes."""

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from bs4 import BeautifulSoup

from src.collectors.company_news import CompanyNewsBundle, NewsItem
from src.config import HOLDINGS
from src.processors.macro_filter import _rebuild_safe_html
from src.processors.news_presentation import publication_text
from src.processors.news_selection import company_candidate, factual_excerpt, plain_source
from src.processors.news_summarizer import _format_input, _rebuild_safe_summary
from src.processors.source_grounding import grounded_text, source_sentences
from src.renderer.news_prose import news_paragraphs

ROWS = json.loads((Path(__file__).parent / 'fixtures/september29_formal_editorial.json').read_text())
NOW = datetime(2026, 9, 29, tzinfo=UTC)


def test_actual_partner_award_is_not_microsoft_news_at_either_publication_gate():
    row = ROWS[0]
    item = NewsItem(row['title'], NOW, 'https://example.com/award', row['source'],
                    summary=row['summary'], holding_ticker='MSFT')
    item.source_excerpt = row['excerpt']
    item.translated_excerpt = row['translation']
    assert not company_candidate(item, 'MSFT')
    holding = next(h for h in HOLDINGS if h.ticker == 'MSFT')
    _, candidates = _format_input([CompanyNewsBundle(holding, [item])])
    assert candidates == []
    assert _rebuild_safe_summary('<strong>微软</strong>——' + row['translation'] + '[1]', [item]) is None
    assert item.title == row['title'] and item.summary == row['summary']


@pytest.mark.parametrize('title,expected', [
    ('Acme wins Microsoft Partner of the Year award', False),
    ('Acme achieves Google Cloud partner designation', False),
    ('Acme 获得微软年度合作伙伴奖', False),
    ('Microsoft signs $10 billion cloud contract with Acme', True),
    ('Microsoft and Acme announce a new data center', True),
    ('Microsoft reports revenue growth of 10%', True),
])
def test_badge_promotion_is_filtered_but_material_partner_business_remains(title, expected):
    assert company_candidate(SimpleNamespace(title=title, summary=''), 'MSFT') is expected


def test_wire_standfirst_is_removed_before_translation_without_rewriting_facts():
    row = ROWS[1]
    item = SimpleNamespace(**row, url='https://example.com/google')
    excerpt = factual_excerpt(item)
    assert excerpt.startswith('Crusoe, the AI factory company, today announced')
    assert excerpt in plain_source(row['summary'])
    assert excerpt in source_sentences(item)
    assert 'GLOBE NEWSWIRE' not in excerpt and 'CLAUDE' not in excerpt
    item.source_excerpt = excerpt
    item.translated_excerpt = 'Crusoe，这家 AI 工厂公司，今日宣布其是 Google 在阿马里洛郊外阿姆斯特朗县正在建设的数据中心园区的开发商。'
    text, mapping = grounded_text(item.translated_excerpt, [item])
    assert text == item.translated_excerpt
    assert mapping[0]['original_summary'] == row['summary']
    assert mapping[0]['excerpt'] == excerpt


@pytest.mark.parametrize('name,code', [('泡泡玛特','09992.HK'), ('泡泡玛特','9992.HK'),
                                     ('腾讯','00700.HK'), ('腾讯','0700.HK')])
def test_hk_listing_metadata_is_removed_without_removing_share_quantities(name, code):
    text = f'{name}（{code}）：9月28日南向资金增持16.6万股。'
    assert publication_text(text) == f'{name}：9月28日南向资金增持16.6万股。'
    assert publication_text(f'{name}（持股16.6万股）') == f'{name}（持股16.6万股）'


def test_actual_pop_mart_preserves_source_code_only_in_audit():
    row = ROWS[2]
    item = SimpleNamespace(**row, url='https://example.com/popmart')
    text, mapping = grounded_text(row['title'], [item])
    assert text == '泡泡玛特：9月28日南向资金增持16.6万股'
    assert '09992.HK' in mapping[0]['original_title']
    assert '16.6' in text


def test_actual_macro_groups_related_topics_with_each_facts_own_citation():
    items = [SimpleNamespace(**r, source_excerpt=r['excerpt'], translated_excerpt=r['translation'],
                             url=f'https://example.com/{i}') for i, r in enumerate(ROWS[3:])]
    evidence = []
    html, notes = _rebuild_safe_html('<p>宏观动态。错误的模型主题和拼接[1][2][3][4]</p>', items, evidence)
    soup = BeautifulSoup(news_paragraphs(html), 'html.parser')
    paragraphs = soup.select('p')
    assert len(paragraphs) == 4
    assert [p.select_one('span').get_text() for p in paragraphs] == ['中东局势。','美债市场。','国防开支。','地缘政治。']
    assert '石油出口' in paragraphs[0].get_text() and '亚洲债券' in paragraphs[1].get_text()
    assert '美国国债' in paragraphs[1].get_text() and '美联储' in paragraphs[1].get_text()
    assert 'Treasuries' not in soup.get_text() and 'Federal Reserve' not in soup.get_text()
    assert [a['href'] for p in paragraphs for a in p.select('a')] == [i.url for i in items]
    assert all(len(p.select('a')) == 1 for p in paragraphs)
    assert len(notes) == len(evidence) == 4
    assert '207 亿美元' in paragraphs[2].get_text()


def test_unknown_topics_do_not_create_a_false_connection():
    items = [SimpleNamespace(title=t, source='Source', summary='', url=f'https://example.com/{i}')
             for i,t in enumerate(['甲国公布统计结果。', '乙国宣布会议日程。'])]
    html, _ = _rebuild_safe_html('<p>全球增长。凭空合并[1][2]</p>', items)
    assert len(BeautifulSoup(html, 'html.parser').select('p')) == 1
    assert '其他宏观。' in html and '凭空合并' not in html


def test_business_date_and_negation_are_not_mistaken_for_a_dateline():
    from src.processors.news_selection import reporting_text
    text = 'Microsoft did not approve the AI plan for September 28, 2026--the review remains pending.'
    assert reporting_text(text) == text


def test_grouped_macro_still_binds_every_fact_to_original_sources_for_judgment():
    from src.processors.macro_filter import MacroNewsSummary
    from src.processors.thesis.extractor import _grounding_material
    items = [SimpleNamespace(**r, source_excerpt=r['excerpt'], translated_excerpt=r['translation'],
                             url=f'https://example.com/{i}') for i,r in enumerate(ROWS[3:])]
    evidence = []
    html, notes = _rebuild_safe_html('<p>宏观动态。组合[1][2][3][4]</p>', items, evidence)
    summary = MacroNewsSummary(html, notes, evidence)
    material = _grounding_material(company_news=None, macro_news=summary, figure_summaries=[], frontier_labs_events=[])
    assert material['macro']['urls'] == {item.url for item in items}
    assert all(len(rows) == 1 for rows in material['macro']['by_url'].values())
