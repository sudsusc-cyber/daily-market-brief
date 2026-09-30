"""Actual final-preview regressions generalized across issuers and topics."""
from types import SimpleNamespace

import pytest

from src.processors.editorial_evidence import editorial_issue
from src.processors.macro_events import extract_event
from src.processors.macro_filter import _rebuild_safe_html
from src.processors.news_selection import factual_excerpt
from src.processors.source_grounding import grounded_text
from src.processors.translation_guard import translation_errors


def item(title, translated='', summary='', index=1):
    return SimpleNamespace(title=title, source_excerpt=title, translated_excerpt=translated,
                           summary=summary, url=f'https://example.com/{index}', source='Source')


@pytest.mark.parametrize('issuer', ['Acme', 'Northstar', 'Apple'])
def test_reader_navigation_does_not_erase_complete_operating_headline(issuer):
    title = f'{issuer} CEO reportedly considers a leaner organization'
    row = item(title + ' – Here’s the key things under consideration')
    row.source_excerpt = title
    row.translated_excerpt = f'据报道，{issuer} CEO 考虑更精简的组织架构'
    assert factual_excerpt(row) == title
    text, evidence = grounded_text(row.translated_excerpt, [row])
    assert text == row.translated_excerpt and evidence[0]['excerpt'] == title


@pytest.mark.parametrize('text', [
    'Bill Gates makes a bold prediction about the future and AI',
    'Acme founder makes a bold prediction about the future',
    'What we know about a meeting with AI leaders',
    'See How an Offensive Was Powered by Arms',
    '长期基本面仍由稳定收入支撑。',
    '该大幅增强的产品组合将于明年推出。',
])
def test_navigation_predictions_and_unresolved_references_are_not_events(text):
    assert editorial_issue(text)


def test_complete_operating_fact_survives_a_prediction_title():
    fact = 'Acme reported revenue growth of 10%.'
    assert factual_excerpt(item('Founder makes a bold prediction about AI', summary=fact)) == fact


def test_reordered_translation_does_not_change_macro_topic():
    original = 'Dollar Powers to Best Month Since June on Fed’s Inflation Fight'
    translated = '美联储抗击通胀，美元飙升至6月以来最佳月度表现'
    assert not translation_errors(original, translated)
    html, notes = _rebuild_safe_html('<p>通胀数据。' + translated + '[1]</p>', [item(original, translated)])
    assert '外汇市场。' in html and '通胀数据。' not in html and len(notes) == 1


def test_one_split_paragraph_cannot_displace_other_top_stories():
    texts = ['美国公布通胀数据。', '美国国债收益率上涨。', '外汇市场走强。',
             '中国政府宣布首套房贷补贴。', '中东战争升级。']
    rows = [item(t, index=i) for i, t in enumerate(texts, 1)]
    evidence, audit = [], []
    html, notes = _rebuild_safe_html(
        '<p>美国市场。[1][2][3]</p><p>房贷补贴。[4]</p><p>中东局势。[5]</p>', rows,
        evidence, editorial_order=True, selection_audit=audit)
    assert {t['paragraph_rank'] for t in audit[0]['themes'] if t['published']} == {0, 1, 2}
    assert html.count('data-macro-heading=') == 3
    assert '财政政策。' in html and '中东局势。' in html
    assert len(notes) == 3 and {e['excerpt'] for e in evidence} == {texts[0], texts[3], texts[4]}


@pytest.mark.parametrize('country', ['China', 'France', 'Canada'])
def test_mortgage_subsidy_is_fiscal_policy_across_countries(country):
    assert extract_event(f'{country} offers subsidies on residential mortgages').topic == '财政政策'


@pytest.mark.parametrize('original,translated', [
    ('Acme reported results beating revenue expectations, with sales up 11.1% to $95.72 billion.',
     'Acme 公布业绩，营收超出预期，销售额增长11.1%至957.2亿美元。'),
    ('Sales surged 11.2% in fiscal 2026.', '2026财年销售额激增11.2%。'),
    ('Scaling energy is a challenge.', '扩大能源规模是一项挑战。'),
    ('The EU introduces restrictions.', '欧盟出台限制措施。'),
    ('Oil steadied after its biggest drop in more than a week.', '油价企稳，此前创下一周多以来最大跌幅。'),
])
def test_preview_context_equivalence(original, translated):
    assert translation_errors(original, translated) == []
