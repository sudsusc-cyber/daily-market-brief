"""Long-term editorial selection: generic noise classes and useful counterexamples."""
from datetime import UTC, datetime
from unittest.mock import Mock

import pytest

from src.collectors.company_news import CompanyNewsBundle, NewsItem
from src.config import Holding
from src.processors.investment_relevance import long_term_noise_reason
from src.processors.news_summarizer import _format_input, _rebuild_safe_summary, summarize


@pytest.mark.parametrize('title', [
    '某品牌推出联名礼盒和新配色',
    '某公司：南向资金增持 16 万股',
    'How to install the latest Acme app',
    'Acme wins an award for customer service',
    '新公司荣获年度品牌大奖',
])
def test_low_information_classes_are_excluded(title):
    assert long_term_noise_reason(title)


@pytest.mark.parametrize('title,summary', [
    ('某品牌推出联名礼盒', '授权收入增长 20%，拓展销售渠道。'),
    ('Acme wins an award', 'The company signed a distribution contract.'),
    ('Apple launches an experimental device', ''),
    ('Microsoft integrated with a new enterprise platform', ''),
    ('Acme appoints a new chief executive', ''),
    ('Acme faces a regulatory investigation', ''),
    ('Acme authorizes a stock buyback', ''),
    ('某品牌推出联名产品', '首次进入海外市场。'),
])
def test_material_and_early_events_are_not_subject_to_numeric_whitelist(title, summary):
    assert not long_term_noise_reason(title, summary)


def test_incidental_platform_mention_differs_from_issuer_product_and_customer_adoption():
    title = 'Acme now integrated with Microsoft to connect social insights with sales data'
    assert long_term_noise_reason(title, holding_is_subject=False) == 'incidental_vendor_integration'
    assert not long_term_noise_reason(title, 'Paid subscribers grew 30%.', holding_is_subject=False)
    assert not long_term_noise_reason(title, holding_is_subject=True)


def source(title):
    return NewsItem(title, datetime(2026, 10, 3, tzinfo=UTC), 'https://example.com/' + str(len(title)), 'Source', holding_ticker='AAPL')


def bundle(items):
    return CompanyNewsBundle(Holding('AAPL', '苹果', 'apple.com'), items)


def test_filter_precedes_candidate_limit():
    noise = [source('How to install Apple software') for _ in range(20)]
    useful = source('Apple launches an experimental device')
    _, selected = _format_input([bundle(noise + [useful])])
    assert selected == [useful]


def test_llm_cannot_reintroduce_low_information_source():
    item = source('Apple 荣获年度品牌大奖')
    item.source_excerpt = item.title
    item.translated_excerpt = item.title
    result = _rebuild_safe_summary('<strong>苹果</strong> — Apple 荣获年度品牌大奖。[1]', [item])
    assert not result or not result.footnotes


def test_editorial_silence_is_audited_without_llm_or_processing_failure():
    client = Mock()
    result = summarize([bundle([source('How to install Apple software')])], client=client)
    assert result.is_silence and not result.summary_html
    assert result.selection_audit[0]['excluded'][0]['reason'] == 'consumer_instruction'
    assert not client.mock_calls
