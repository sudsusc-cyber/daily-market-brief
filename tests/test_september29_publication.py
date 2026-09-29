"""Reproduce the September 29 actual publication, without live APIs or SMTP."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from bs4 import BeautifulSoup

from src.processors.macro_filter import _rebuild_safe_html
from src.processors.news_selection import (
    chinese_prose,
    company_candidate,
    complete_excerpt,
    factual_excerpt,
)
from src.processors.source_grounding import grounded_text
from src.processors.translation_guard import translation_errors

ROWS = json.loads((Path(__file__).parent / 'fixtures/september29_news.json').read_text())


@pytest.mark.parametrize('row', ROWS)
def test_recorded_correct_translations_survive_without_raw_english_fallback(row):
    assert not translation_errors(row['excerpt'], row['translation'])


@pytest.mark.parametrize('original,wrong', [
    ('Costco revenue rose 11% to $95.72 billion.', 'Costco 营收下降11%至957.2亿美元。'),
    ('Costco revenue rose 11% to $95.72 billion.', 'Costco 营收增长11%至957.2亿港元。'),
    ('Costco revenue rose 11% to $95.72 billion.', 'Costco 营收增长15%至957.2亿美元。'),
    ('U.K. Releases Suspects', '英国发布产品'),
    ('U.K. Releases Suspects', '英国逮捕嫌疑人'),
    ('OpenAI releases a model', 'OpenAI 释放嫌疑人'),
    ('Apple is ordered to pay $5.7 billion.', 'Apple 已支付57亿美元。'),
    ('Dividend payable on November 10, 2026', '股息已支付，日期为2026年11月10日'),
    ('Fed has not approved the plan', '美联储已批准计划'),
    ('Microsoft will acquire Google', 'Microsoft 已收购Google'),
    ('Latest Oil News for Sept. 29', '9月30日最新石油新闻'),
    ('Costco revenue rose 11%, while EPS increased 15%.', 'Costco 营收增长15%，EPS增长11%。'),
    ('Costco revenue rose 11%, while EPS fell 15%.', 'Costco 营收下降11%，EPS增长15%。'),
])
def test_word_sense_fixes_do_not_accept_changed_facts(original, wrong):
    assert translation_errors(original, wrong)


@pytest.mark.parametrize('text', [
    '几素与泡泡玛特宣布推出首款以Twinkle Twinkle为',
    '公司宣布新产品……', '公司宣布新产品...', '公司宣布新产品用于。',
])
def test_incomplete_source_cannot_be_published_by_adding_a_period(text):
    item = SimpleNamespace(title=text, summary='', url='https://example.com/partial')
    assert not complete_excerpt(text)
    assert grounded_text(text, [item]) == ('', [])


def test_failed_translation_is_not_an_english_body_or_a_fake_chinese_prefix():
    item = SimpleNamespace(title='Microsoft has not approved the acquisition.', summary='',
                           translated_title='Microsoft 已批准收购。', url='https://example.com/source')
    assert grounded_text('unmatched', [item]) == ('', [])
    assert not chinese_prose('原文 Microsoft has not approved the acquisition and has no plans to do so')


def test_native_chinese_remains_publishable_without_raw_excerpt_label():
    item = SimpleNamespace(title='腾讯回购227000股，耗资1.002亿港元 - 东方财富', source='东方财富',
                           summary='', url='https://example.com/tencent')
    text, mapping = grounded_text('unmatched', [item])
    assert text == '腾讯回购227000股，耗资1.002亿港元'
    assert mapping[0]['mode'] == 'verified_extract'


def test_real_rating_service_and_truncated_pop_mart_are_not_company_news():
    for row, ticker in [(ROWS[4], 'MCO'), (ROWS[10], '9992.HK')]:
        assert not company_candidate(SimpleNamespace(**row), ticker)
    assert company_candidate(SimpleNamespace(title='Moody’s reports revenue growth of 10%', summary=''), 'MCO')


def test_commentary_and_rolling_page_titles_choose_facts_or_are_excluded():
    assert factual_excerpt(SimpleNamespace(**ROWS[7])) == 'Coca-Cola Hired Monster’s Americas CEO to Run North America.'
    oil = next(row for row in ROWS if row['title'].startswith('Latest Oil'))
    assert factual_excerpt(SimpleNamespace(**oil)) == oil['summary']
    assert not company_candidate(SimpleNamespace(**ROWS[0]), 'MSFT')


def test_macro_unrelated_articles_are_separate_and_rejected_links_are_absent():
    items = [SimpleNamespace(title=t, summary='', source='Source', url=f'https://example.com/{i}')
             for i, t in enumerate(['油价上涨，市场关注供应。', '英国释放恐怖阴谋嫌疑人。', 'English only source'])]
    evidence = []
    html, footnotes = _rebuild_safe_html('<p>宏观动态。模型胡乱合并[1][2][3]</p>', items, evidence)
    soup = BeautifulSoup(html, 'html.parser')
    assert len(soup.select('p')) == 2
    assert len(footnotes) == len(evidence) == 2
    assert '能源市场' in soup.select('p')[0].get_text()
    assert '地缘政治' in soup.select('p')[1].get_text()
    assert [a['href'] for a in soup.select('a')] == [x.url for x in items[:2]]
    assert all(len(p.select('a')) == 1 for p in soup.select('p'))


@pytest.mark.parametrize('original,translated', [
    ('Oil rose a second day', '油价连续第二天上涨'),
    ('Anthropic launched another model in less than a week', 'Anthropic 在不到一周内推出另一款模型'),
])
def test_written_time_intervals_keep_the_same_duration(original, translated):
    assert not translation_errors(original, translated)
    assert translation_errors(original, translated.replace('第二天', '第三天').replace('一周', '两周'))


@pytest.mark.parametrize('row', [r for r in ROWS if r['section'] == 'figures'])
def test_translated_publisher_suffix_stays_in_footnotes_not_body(row):
    item = SimpleNamespace(**row, translated_excerpt=row['translation'],
                           source_excerpt=row['excerpt'], url='https://example.com/figure')
    text, mapping = grounded_text(row['translation'], [item])
    assert text and row['source'] not in text
    assert mapping[0]['source_name'] == row['source']
    assert mapping[0]['validated_text'] == row['translation']
    assert mapping[0]['original_title'] == row['title']


def test_same_macro_fact_retains_both_sources_in_one_paragraph():
    items = [SimpleNamespace(title='美联储维持利率不变。', summary='', source=f'Source {i}',
                             url=f'https://example.com/{i}') for i in range(2)]
    html, footnotes = _rebuild_safe_html('<p>货币政策。美联储维持利率不变。[1][2]</p>', items)
    soup = BeautifulSoup(html, 'html.parser')
    assert len(soup.select('p')) == 1
    assert len(soup.select('a')) == len(footnotes) == 2


def test_company_rejected_english_source_does_not_leave_an_unpublished_footnote():
    from datetime import UTC, datetime

    from src.collectors.company_news import NewsItem
    from src.processors.news_summarizer import _rebuild_safe_summary

    items = [NewsItem(title, datetime(2026, 9, 29, tzinfo=UTC), f'https://example.com/{i}',
                      'Source', holding_ticker='MSFT') for i, title in enumerate([
                          '微软公布季度业绩。', 'Microsoft announced earnings.'])]
    result = _rebuild_safe_summary('<strong>微软</strong>——无根据的拼接[1][2]', items)
    assert result is not None
    assert len(result.footnotes) == len(result.evidence) == 1
    assert result.footnotes[0].url == items[0].url
    assert items[1].url not in result.summary_html
