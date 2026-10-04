from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from bs4 import BeautifulSoup

from src.collectors.macro_news import MacroNewsItem
from src.processors.macro_filter import _rebuild_safe_html
from src.processors.news_selection import company_candidate, factual_excerpt
from src.processors.thesis.renderer import _rule_for

NOW = datetime(2026, 10, 4, tzinfo=UTC)


@pytest.mark.parametrize('issuer,ticker', [('Costco', 'COST'), ('Microsoft', 'MSFT'), ('Nvidia', 'NVDA')])
@pytest.mark.parametrize('value', [17, 23])
def test_valuation_opinions_are_not_operating_news_even_with_numeric_claims(issuer, ticker, value):
    item = SimpleNamespace(title=f'{issuer} Stock May Be {value}% Overvalued Following Sales News',
                           summary='Recent earnings news is on investors minds.', source='Source',
                           holding_ticker=ticker, published_at=NOW)
    assert not company_candidate(item, ticker)
    assert not factual_excerpt(item)


@pytest.mark.parametrize('title,summary', [
    ("Alphabet Reported Negative Free Cash Flow for the First Time Ever. Here's Why That Milestone Matters for Shareholders.", 'Investors should get comfortable owning a capital-intensive business.'),
    ('ASML vs. Taiwan Semiconductor: What Revenue Trends Tell Investors About These Companies', "TSMC outpaced ASML in revenue for eight quarters, but acceleration hints at a shifting competitive dynamic."),
])
def test_investor_comparisons_and_undated_milestones_do_not_pose_as_daily_reports(title, summary):
    item = SimpleNamespace(title=title, summary=summary, source='Source', published_at=NOW)
    assert not company_candidate(item, 'GOOG' if title.startswith('Alphabet') else 'TSM')


def test_analysis_packaging_can_still_supply_an_independent_operating_announcement():
    summary = 'Microsoft reported free cash flow of $5 billion today.'
    item = SimpleNamespace(title="Microsoft's Milestone: Here's Why It Matters for Shareholders", summary=summary,
                           source='Source', published_at=NOW, holding_ticker='MSFT')
    assert factual_excerpt(item) == summary
    assert company_candidate(item, 'MSFT')


def test_spending_translation_supplies_the_same_bounded_infrastructure_watchpoint():
    row = dict(excerpt='Anthropic plans $518 billion AI infrastructure spend.',
               output_text='Anthropic 计划5180亿美元的AI基础设施支出。')
    assert _rule_for(row).key == 'infrastructure-investment'
    assert _rule_for(dict(row, output_text='Anthropic 讨论AI基础设施。')) is None


def macro_rows():
    texts = ['伊朗宣布恢复石油运输。', '伊拉克调整石油供应路线。', '沙特宣布维持石油产量。', '中东国家公布新的能源运输安排。']
    return [MacroNewsItem(t, NOW + timedelta(minutes=i), f'https://example.com/{i}', 'Source') for i,t in enumerate(texts)]


def test_theme_selection_caps_whole_facts_keeps_newer_updates_and_renumbers_sources():
    rows = macro_rows()
    evidence, audit = [], []
    body, notes = _rebuild_safe_html('<p>[1][2][3][4]</p>', rows, evidence,
                                    editorial_order=True, selection_audit=audit)
    soup = BeautifulSoup(body, 'html.parser')
    assert len(soup.select('p')) == 1
    assert len(soup.select('[data-macro-fact]')) == 3
    assert rows[0].title not in soup.get_text()
    assert all(item.title in soup.get_text() for item in rows[1:])
    assert [n.index for n in notes] == [1,2,3]
    assert {n.url for n in notes} == {r['url'] for r in evidence}
    budget = next(a for a in audit if a['phase'] == 'theme_fact_selection')
    assert budget['omitted_urls'] == [rows[0].url]
    theme = next(a for a in audit if a['phase'] == 'theme_selection')['themes'][0]
    assert rows[0].url not in theme['published_urls']
    assert set(theme['published_urls']) == {n.url for n in notes}
    assert [i.title for i in rows] == [i.title for i in macro_rows()]


def test_grouping_already_published_facts_does_not_apply_editorial_clipping():
    evidence = []
    body, notes = _rebuild_safe_html('<p>[1][2][3][4]</p>', macro_rows(), evidence)
    assert len(BeautifulSoup(body,'html.parser').select('[data-macro-fact]')) == 4
    assert len(notes) == len(evidence) == 4


def test_long_leading_fact_is_kept_whole_instead_of_silently_truncated():
    text = '伊拉克宣布调整石油运输路线，' + '具体安排仍需观察，' * 25 + '最终方案尚待公布。'
    item = MacroNewsItem(text, NOW, 'https://example.com/long', 'Source')
    body, notes = _rebuild_safe_html('<p>[1]</p>', [item], editorial_order=True)
    assert text in BeautifulSoup(body, 'html.parser').get_text()
    assert len(notes) == 1
