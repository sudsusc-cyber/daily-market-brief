"""Keep the observed October 13-14 mail defects from recurring."""

from datetime import UTC, datetime
from types import SimpleNamespace

from src.collectors.sentiment import SentimentBundle, SentimentMetric
from src.processors.editorial_evidence import editorial_issue
from src.processors.news_presentation import present
from src.processors.news_selection import factual_excerpt, publishable_excerpt
from src.processors.sentiment_judge import _argument_errors
from src.processors.source_grounding import grounded_text


def test_truce_announcement_cannot_hide_immediate_violation():
    first = 'Trump announces a truce between Russia and Ukraine.'
    item = SimpleNamespace(
        title='Opinion | Putin Ignores That Energy “Cease-Fire”',
        summary=first + ' Russia immediately violates it.', snippet='',
        source_excerpt=first, translated_excerpt='特朗普宣布俄罗斯与乌克兰达成停火。',
        source='WSJ', url='https://www.wsj.com/opinion/energy-cease-fire',
        published_at=datetime(2026, 10, 12, tzinfo=UTC),
    )
    assert not publishable_excerpt(item, first)
    assert grounded_text('特朗普宣布俄罗斯与乌克兰达成停火。', [item]) == ('', [])


def test_independent_announcement_remains_publishable():
    first = 'The two countries announce an agreement.'
    item = SimpleNamespace(
        title='Countries announce an agreement', summary=first + ' Talks continue tomorrow.',
        snippet='', source='Wire', url='https://example.com/agreement',
        published_at=datetime(2026, 10, 12, tzinfo=UTC),
    )
    assert publishable_excerpt(item, first)


def test_chart_teaser_without_fact_is_rejected():
    assert editorial_issue('Linde Is Powering the AI Revolution: Chart of the Day')
    assert editorial_issue('正在为 AI 革命提供动力：今日图表')
    item = SimpleNamespace(
        title='Linde Is Powering the AI Revolution: Chart of the Day',
        summary='Linde is securing multiyear gas supply contracts that are essential to advanced semiconductor fabrication.',
        snippet='', holding_ticker='LIN', source='Yahoo',
        published_at=datetime(2026, 10, 13, tzinfo=UTC),
    )
    assert factual_excerpt(item) == item.summary


def test_riyadh_metonym_and_city_are_localized_separately():
    assert present('在 Riyadh 领导的联军打击目标后进行报复。').text == '在沙特领导的联军打击目标后进行报复。'
    assert present('Riyadh 的机场遭到袭击。').text == '利雅得的机场遭到袭击。'


def test_primary_sentiment_levels_cannot_both_be_called_neutral():
    bundle = SentimentBundle([
        SentimentMetric('CNN Fear & Greed', 45.742857, 45.0, None),
        SentimentMetric('VIX', 15.21, 14.84, None),
        SentimentMetric('DXY', 102.572, 102.21, None),
        SentimentMetric('Shiller PE', 41.85, None, None),
        SentimentMetric('高收益债利差', 3.15, 3.09, None, unit='%'),
    ], datetime(2026, 10, 12, tzinfo=UTC))
    text = 'CNN 当前 45.74、VIX 当前 15.21，均处中性区间。'
    assert 'primary_levels_not_both_neutral' in _argument_errors(text, bundle, '中性')
