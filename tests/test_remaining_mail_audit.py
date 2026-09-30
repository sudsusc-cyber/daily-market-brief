"""Replays of the actual September 29 mail plus bounded edge-case regressions."""
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from bs4 import BeautifulSoup

from src.collectors.figures import FigureBundle, FigureMention
from src.collectors.sentiment import SentimentBundle, SentimentMetric
from src.processors.figure_filter import filter_one
from src.processors.news_selection import (
    company_candidate,
    complete_excerpt,
    factual_excerpt,
    macro_candidate,
    old_event_excerpt,
)
from src.processors.source_grounding import grounded_text
from src.processors.thesis.extractor import _verified_grounding_row
from src.processors.translation_guard import translation_errors
from src.renderer.render import render_email

ROWS = json.loads((Path(__file__).parent / 'fixtures/september29_remaining_audit.json').read_text())
NOW = datetime(2026, 9, 29, 0, 22, tzinfo=UTC)


def item(name):
    row = ROWS[name]
    return SimpleNamespace(title=row['original_title'], summary=row['original_summary'],
                           published_at=row['published_at'], source=row['source_name'], url=row['url'])


def test_actual_apple_legal_cost_mistranslation_rejected_and_case_excerpt_selected():
    article = item('apple')
    assert translation_errors(article.title, ROWS['apple']['validated_text'])
    assert company_candidate(article, 'AAPL')
    assert factual_excerpt(article) == article.summary
    article.source_excerpt = article.summary
    article.translated_excerpt = '一宗英国经销商案件重新审理，此前美国作出57亿美元专利裁决，Apple 计划就该裁决上诉。'
    text, evidence = grounded_text(article.translated_excerpt, [article])
    assert text == article.translated_excerpt
    assert evidence[0]['excerpt'] == article.summary
    assert evidence[0]['original_title'] == article.title


@pytest.mark.parametrize('source,translation', [
    ('Apple legal bills stack up', 'Apple 法律费用不断累积'),
    ('Congress passes two legal bills', '国会通过两项法律议案'),
])
def test_legal_sense_guard_keeps_correct_cost_and_legislative_language(source, translation):
    assert not translation_errors(source, translation)


def test_flattened_amex_publisher_footnote_cannot_become_a_financial_number():
    article = item('amex')
    assert not complete_excerpt(ROWS['amex']['excerpt'])
    assert factual_excerpt(article) == article.title
    article.source_excerpt = article.title
    article.translated_excerpt = 'American Express 现已在全球超过1.9亿个商户地点被受理；美国以外的受理地点在过去四年里翻了一倍以上'
    text, evidence = grounded_text(article.translated_excerpt, [article])
    assert text == article.translated_excerpt
    assert '受理1' not in text
    assert evidence[0]['original_summary'] == article.summary  # Original remains immutable.
    assert 'worldwide1' in evidence[0]['original_summary']
    assert complete_excerpt('American Express serves 190 million merchants worldwide.')


def test_newly_published_old_mastercard_recap_is_not_fresh_company_news_or_thesis_evidence():
    article = item('mastercard')
    excerpt = ROWS['mastercard']['excerpt']
    assert old_event_excerpt(article, excerpt)
    assert not company_candidate(article, 'MA')
    assert factual_excerpt(article) == ''
    article.source_excerpt = excerpt
    article.translated_excerpt = ROWS['mastercard']['validated_text']
    assert grounded_text(article.translated_excerpt, [article]) == ('', [])
    row = dict(ROWS['mastercard'], output_text=article.translated_excerpt, mode='checked_translation')
    assert _verified_grounding_row(SimpleNamespace(summary_html=article.translated_excerpt), row) is None


@pytest.mark.parametrize('text,published,old', [
    ('On September 22, Mastercard launched settlement.', '2026-09-28', True),
    ('On September 27, Mastercard launched settlement.', '2026-09-28', False),
    ('9月22日，腾讯发布新产品。', '2026-09-28', True),
    ('2025年9月22日，腾讯发布新产品。', '2026-09-28', True),
    ('On December 22, Mastercard launched settlement.', '2027-01-02', True),
    ('On December 22, Mastercard will launch settlement.', '2027-01-02', False),
    ('On December 22, Mastercard launches settlement.', '2026-05-01', False),
    ('On September 22, Mastercard launched settlement; today it announced new customers.', '2026-09-28', False),
    ('Mastercard today announced new customers after its September 22 launch.', '2026-09-28', False),
    ('On February 30, Mastercard launched settlement.', '2026-09-28', False),
    ('Mastercard launched settlement in 2020, with new capacity announced today.', '2026-09-28', False),
])
def test_event_recency_is_anchored_to_publication_without_rejecting_new_updates(text, published, old):
    assert old_event_excerpt(SimpleNamespace(title='Could Mastercard gain growth?', published_at=published), text) is old


def test_new_update_in_old_company_story_remains_eligible():
    article = item('mastercard')
    article.summary = ROWS['mastercard']['excerpt'] + ' Today Mastercard announced a new settlement customer.'
    assert company_candidate(article, 'MA')
    assert factual_excerpt(article) == 'Today Mastercard announced a new settlement customer.'


def test_actual_local_crime_colour_not_macro_but_market_effects_still_eligible():
    article = item('local_crime')
    assert not macro_candidate(article)
    article.summary += ' The incident halted oil supply and shipping through the port.'
    assert macro_candidate(article)
    assert macro_candidate(SimpleNamespace(title='U.K. Releases Suspects in U.S. Bomber Base Terror Plot', summary=''))
    assert macro_candidate(SimpleNamespace(title='Raytheon Gets $20.7 Billion Missile Contract', summary=''))


def test_sentiment_numbers_have_consistent_precision_and_compilation_time_is_not_data_time():
    sentiment = SentimentBundle([SentimentMetric('DXY', 101.2440032959, 100.9700012207, None)], NOW)
    html = render_email(signals=[], generated_at=NOW, sentiment=sentiment,
                        valuation_checked_at=datetime(2026, 9, 27, tzinfo=UTC))
    text = BeautifulSoup(html, 'html.parser').get_text(' ', strip=True)
    assert all(value in text for value in ['101.24', '100.97', '+0.27'])
    assert '本期编制于北京时间 08:22' in text
    assert '编制时间' in text and '数据截至北京时间' not in text
    assert '09-29' in text


def mention(title, suffix='1'):
    return FigureMention(title, '', NOW, f'https://example.com/{suffix}', 'Reuters')


def figure_bundle(items, error=None):
    return FigureBundle(person='黄仁勋', query='Jensen Huang', person_en='Jensen Huang', items=items, error=error)


def llm(*outputs):
    client = Mock()
    client.chat.side_effect = [x if isinstance(x, Exception) else SimpleNamespace(text=x, error='timeout' if x is None else None)
                               for x in outputs]
    return client


FACT = '黄仁勋明确表示将投资100亿美元建设数据中心。'
YES = f'▦ 1: yes | score=5 | {FACT}'


def test_figure_content_rejection_keeps_verified_neighbor_and_does_not_retry_selection():
    client = llm(YES + '\n▦ 2: yes | score=5 | 没有原文支持的断言', None)
    result = filter_one(figure_bundle([mention(FACT), mention('Jensen said a cloud deal was announced', '2')]), client=client)
    assert [row.text for row in result.items] == [FACT]
    assert result.error and result.content_rejections and not result.processing_error
    assert client.chat.call_count == 2
    assert "逐条翻译" in client.chat.call_args_list[1].kwargs["task_extra"]
    assert client.chat.call_args_list[1].kwargs["timeout"] == 20


def test_figure_partial_protocol_then_timeout_preserves_supported_item_and_marks_processing_error():
    result = filter_one(figure_bundle([mention(FACT), mention('Jensen said another cloud deal was announced', '2')]), client=llm(YES, None))
    assert [row.text for row in result.items] == [FACT]
    assert result.processing_error and result.error


def test_figure_duplicate_decisions_are_not_published_but_independent_facts_survive():
    other = '黄仁勋明确表示将投资200亿美元建设数据中心。'
    response = YES + '\n▦ 1: no | score=1 | 不收录\n▦ 2: yes | score=5 | ' + other
    result = filter_one(figure_bundle([mention(FACT), mention(other, '2')]), client=llm(response, response))
    assert [row.text for row in result.items] == [other]
    assert result.processing_error


def test_figure_exact_duplicate_deduplicates_but_changed_amount_survives():
    other = FACT.replace('100', '200')
    response = YES + '\n▦ 2: yes | score=5 | ' + FACT + '\n▦ 3: yes | score=5 | ' + other
    result = filter_one(figure_bundle([mention(FACT), mention(FACT, '2'), mention(other, '3')]), client=llm(response))
    assert [row.text for row in result.items] == [FACT, other]
    assert not result.error


@pytest.mark.parametrize('items', [[], [mention('Great to see!')]])
def test_figure_source_failure_survives_empty_or_editorially_filtered_candidates(items):
    client = llm()
    result = filter_one(figure_bundle(items, error='RSS timeout'), client=client)
    assert result.error == 'RSS timeout'
    assert not result.processing_error and not result.content_rejections
    client.chat.assert_not_called()


def test_figure_exceptions_are_bounded_and_redacted():
    result = filter_one(figure_bundle([mention(FACT)]), client=llm(RuntimeError('timeout'), RuntimeError('timeout api_key=fake-private-token')))
    assert result.processing_error == 'RuntimeError: timeout api_key=***'
    assert 'fake-private-token' not in result.error
    assert not result.items


def test_interview_reporting_and_weekend_news_do_not_inherit_daily_recap_rule():
    text = 'On September 25, Jensen said capacity would increase.'
    assert not old_event_excerpt(SimpleNamespace(title='Jensen interview about capacity', published_at='2026-09-28'), text)


def test_old_recap_undated_continuation_cannot_bypass_source_or_thesis_gate():
    article = item('mastercard')
    continuation = 'Mastercard launched stablecoin settlement.'
    article.summary += ' ' + continuation
    article.source_excerpt = continuation
    article.translated_excerpt = 'Mastercard 推出稳定币结算。'
    assert grounded_text(article.translated_excerpt, [article]) == ('', [])
    row = dict(ROWS['mastercard'], original_summary=article.summary, excerpt=continuation,
               output_text=article.translated_excerpt, validated_text=article.translated_excerpt, mode='checked_translation')
    assert _verified_grounding_row(SimpleNamespace(summary_html=article.translated_excerpt), row) is None
