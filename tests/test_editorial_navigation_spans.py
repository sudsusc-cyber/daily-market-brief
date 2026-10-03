from datetime import UTC, datetime
from types import SimpleNamespace

from src.collectors.macro_news import MacroNewsItem
from src.processors.macro_filter import _rebuild_safe_html
from src.processors.news_selection import company_candidate, factual_excerpt, publishable_excerpt
from src.processors.source_grounding import source_sentences


def test_independent_report_is_kept_without_stock_teaser():
    title='Acme wins an AI compute deal with Northstar. What That Means for ACME Stock. - News'
    item=SimpleNamespace(title=title,summary='',source='News')
    assert factual_excerpt(item)=='Acme wins an AI compute deal with Northstar.'
    assert factual_excerpt(item) in source_sentences(item)
    assert not publishable_excerpt(item,'What That Means for ACME Stock.')
    item.title='Acme wins an AI compute deal. Northstar raises its guidance.'
    assert not factual_excerpt(item).endswith('deal.')


def test_price_headline_does_not_take_a_holding_slot_from_reported_operations():
    item=SimpleNamespace(title='SPCX Stock Climbs As Three Launches Meet Start Of Google’s AI Pact',summary='',source='News',holding_ticker='GOOG')
    assert not company_candidate(item,'GOOG')
    item.summary='Google reported revenue of $50 billion.'
    assert company_candidate(item,'GOOG')


def test_commentary_label_does_not_claim_a_data_release():
    row=MacroNewsItem('CEA Chair Says Inflation Coming Down Sufficiently Fast',datetime(2026,10,3,tzinfo=UTC),'https://example.com/a','News','')
    row.source_excerpt=row.title
    row.translated_excerpt='CEA 主席称通胀下降速度足够快。'
    html,_=_rebuild_safe_html('<p>[1]</p>',[row])
    assert '通胀观察' in html and '通胀数据' not in html
