"""Offline regressions for the October 6 delivered edition and analogous cases."""
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from src.collectors import news_context
from src.collectors.figures import FigureBundle, FigureMention, _passes_first_filter
from src.collectors.sentiment import SentimentBundle, SentimentMetric
from src.processors.figure_filter import _RecoveryBudget, candidate_window, filter_one
from src.processors.news_presentation import present, replay_presentation
from src.processors.sentiment_judge import _argument_errors, score_sentiment
from src.processors.speaker_attribution import attribution
from src.processors.thesis.renderer import _named_product
from src.processors.translator import _parse_lines, translate_titles
from src.utils.quality_details import quality_details
from src.utils.runtime_budget import (
    RuntimeBudget,
    StageTimeout,
    reset_timeout_events,
    timeout_events,
)

NOW = datetime(2026, 10, 5, 20, tzinfo=UTC)


def mention(title, source='Source', url='https://example.com/voice'):
    return FigureMention(title, '', NOW, url, source)


@pytest.mark.parametrize('term', ['Up 500%', 'Down 20%', 'Higher 25 percent', 'Lower 10 bps', 'October 6', 'Sales 100 Million'])
def test_financial_changes_and_dates_are_not_product_names(term):
    assert _named_product('Company Revenue ' + term + ' This Year') is None


@pytest.mark.parametrize('product', ['Model 3', 'GPT-5', 'MI 450', 'Gemini 3.5 Pro'])
def test_named_products_still_survive(product):
    assert _named_product('Company announces ' + product + ' today')[0] == product


def test_navigation_and_repeated_standfirst_preserve_reporting_and_old_replay():
    source = '10 月 5 日耗资 1 亿港元回购 23.8 万股_股市直播_市场。'
    assert present(source).text == '10 月 5 日耗资 1 亿港元回购 23.8 万股。'
    assert '_股市直播' not in present(source.rstrip('。') + '\xa0\xa0中金在线', source_name='中金在线', original_text=source.rstrip('。') + '\xa0\xa0中金在线').text
    assert present('发布 Product_Market_News。').text == '发布 Product_Market_News。'
    raw = '一个数字：每位员工 720 万美元收入 NVIDIA（NASDAQ:NVDA）现在每名在册员工带来约 720 万美元收入。'
    result = present(raw, original_text=raw)
    assert result.text == 'NVIDIA现在每名在册员工带来约 720 万美元收入。'
    assert 'repeated_standfirst' in result.operations
    assert '一个数字' in replay_presentation(raw, {'presentation_version': 16})
    # A unique financial fact in the heading cannot be silently removed.
    assert '800' in present(raw.replace('每位员工 720', '每位员工 800')).text
    assert '一个数字' in present(raw.replace('每位员工 720 万美元收入', '每位员工 720 万美元利润')).text
    assert '一个数字' in present(raw.replace('每位员工 720 万美元收入', '每位员工收入未达 720 万美元')).text


@pytest.mark.parametrize('wrapper', ['curly', 'json', 'double_escaped_json'])
def test_complete_multiline_transport_decodes_before_fact_verification(wrapper):
    original = 'China closes banks\nMore than 670 lenders closed last year.'
    translated = '中国关闭银行\n去年有超过 670 家贷款机构关闭。'
    if wrapper == 'curly':
        body = '“' + translated.replace('\n', r'\n') + '”'
    elif wrapper == 'double_escaped_json':
        body = json.dumps(translated.replace('\n', r'\n'), ensure_ascii=False)
    else:
        body = json.dumps(translated, ensure_ascii=False)
    client = SimpleNamespace(chat=lambda *a, **kw: SimpleNamespace(text='▦ 1: ' + body, error=None))
    assert translate_titles([original], client=client) == ['中国关闭银行。去年有超过 670 家贷款机构关闭。']
    wrong = body.replace('670', '760')
    bad_client = SimpleNamespace(chat=lambda *a, **kw: SimpleNamespace(text='▦ 1: ' + wrong, error=None))
    assert translate_titles([original], client=bad_client) == [original]


def test_partial_unwrapped_transport_is_not_repaired():
    for body in [r'"中国关闭银行\n机构关闭', r'中国关闭银行\n机构关闭']:
        assert _parse_lines('▦ 1: ' + body)[1] == body


@pytest.mark.parametrize('metadata', [
    '<meta property="article:published_time" content="2026-10-05T12:00:00Z">',
    '<time itemprop="datePublished" datetime="2026-10-05T12:00:00Z"></time>',
])
def test_article_publication_metadata_requires_exact_identity(metadata):
    title = 'Jane Smith says AI demand could increase.'
    assert news_context._published_at('<h1>' + title + '</h1>' + metadata, title, '')
    assert not news_context._published_at('<h1>Another story</h1>' + metadata, title, '')
    assert not news_context._published_at('<h1>' + title + '</h1>' + metadata.replace('datePublished', 'dateModified').replace('published_time', 'modified_time'), title, '')
    og = '<meta property="og:title" content="' + title + '">'
    assert news_context._published_at(og + metadata, title, '')
    assert not news_context._published_at('<h1>Other story</h1>' + og + metadata, title, '')


def test_nbc_mirror_recovers_real_date_but_not_old_or_wrong_speaker(monkeypatch):
    title = 'Sam Altman says AI could change work.'
    item = mention(title + ' - NBC News', 'NBC News', 'https://news.google.com/rss/articles/fixture')
    bundle = FigureBundle('奥特曼', 'query', 'Sam Altman', [item])
    monkeypatch.setattr(news_context, '_resolve', lambda url: 'https://www.nbcnews.com/story')
    def html(day):
        return ('<h1>' + title + '</h1><meta property="article:published_time" content="' + day + '">').encode()
    monkeypatch.setattr(news_context, '_fetch', lambda url: html('2026-10-05T12:00:00Z'))
    news_context.enrich_speaker_context([bundle])
    assert attribution(item, '奥特曼', 'Sam Altman', excerpt=title)
    assert not attribution(item, 'Dario Amodei', excerpt=title)
    item.source_published_at = ''
    monkeypatch.setattr(news_context, '_fetch', lambda url: html('2026-08-31T12:00:00Z'))
    news_context.enrich_speaker_context([bundle])
    assert not attribution(item, '奥特曼', 'Sam Altman', excerpt=title)
    assert item.published_at == NOW
    with pytest.raises(ValueError):
        news_context._publisher_url('https://www.nbcnews.com.evil.test/story')


def test_date_budget_is_not_consumed_first_by_another_person_in_search_bucket(monkeypatch):
    wrong = mention('Other Person says AI could change work. - CNBC', 'CNBC', 'https://news.google.com/rss/articles/wrong')
    right = mention('Jane Smith says AI could change work. - NBC News', 'NBC News', 'https://news.google.com/rss/articles/right')
    calls = []
    monkeypatch.setattr(news_context, '_resolve', lambda url: 'https://www.nbcnews.com/right' if url.endswith('right') else 'https://www.cnbc.com/wrong')
    def fetch(url):
        calls.append(url)
        title = right.title.removesuffix(' - NBC News')
        return ('<h1>' + title + '</h1><meta property="article:published_time" content="2026-10-05T12:00:00Z">').encode()
    monkeypatch.setattr(news_context, '_fetch', fetch)
    news_context.enrich_speaker_context([FigureBundle('Jane Smith', 'query', items=[wrong, right])], max_requests=1)
    assert calls == ['https://www.nbcnews.com/right']
    assert right.source_published_at
    assert not getattr(wrong, 'source_published_at', '')


def test_named_supported_candidates_outrank_noise_and_literal_duplicates():
    noise = [mention('Another Person says shares will rise.', url=f'https://example.com/{i}') for i in range(5)]
    strong = mention('Sam Altman tells lawmakers AI could change work.', 'NBC News', 'https://www.nbcnews.com/story')
    duplicate = mention(strong.title, 'NBC News', 'https://www.nbcnews.com/copy')
    other = mention('Sam Altman says safety could affect AI adoption.', 'Fortune', 'https://fortune.com/story')
    bundle = FigureBundle('奥特曼', 'query', 'Sam Altman', [*noise, strong, duplicate, other])
    result = candidate_window(bundle, limit=2)
    assert result == [strong, other]
    assert len(candidate_window(bundle, limit=20)) == 8


def test_current_speech_can_compare_historical_years_but_old_speech_cannot_refresh():
    assert _passes_first_filter(mention('黄仁勋表示收入比2024年增长。'))
    assert not _passes_first_filter(mention('黄仁勋在2024年表示收入会增长。'))
    assert not _passes_first_filter(mention('Jane Smith said in 2024 demand would grow.'))
    assert _passes_first_filter(mention('Jane Smith says demand is above the level in 2024.'))


@pytest.mark.parametrize('verb', ['tells', 'explains', 'describes', 'characterizes'])
def test_speech_grammar_is_shared_by_collection_and_selection(verb):
    item = mention('Jane Smith ' + verb + ' why chip demand could increase.')
    assert _passes_first_filter(item)
    assert candidate_window(FigureBundle('Jane Smith', 'query', items=[item])) == [item]


def test_empty_first_window_backfills_once_with_shared_budget_and_keeps_diagnostics(monkeypatch):
    first = mention('Jane Smith says AI demand could increase.')
    second = mention('Jane Smith says chip demand could increase.', url='https://example.com/second')
    for item in [first, second]:
        item.source_excerpt = item.title
        item.translated_excerpt = item.title.replace('says', '表示').replace('demand could increase', '需求可能增长')
    first.speaker_date_required = True
    responses = iter(['▦ 1: yes | score=4 | ' + first.title, '▦ 1: yes | score=4 | ' + second.title])
    client = SimpleNamespace(chat=lambda *a, **kw: SimpleNamespace(text=next(responses), error=None))
    monkeypatch.setattr(news_context, 'enrich_speaker_context', lambda *a, **kw: None)
    budget = _RecoveryBudget(selection_calls=1)
    result = filter_one(FigureBundle('Jane Smith', 'query', items=[first, second]), client=client, max_items=1, recovery_budget=budget)
    assert result.items and result.items[0].source_url == second.url
    assert budget.selection_calls == 0
    assert any('speaker_date_unverified' in x for x in result.content_rejections)
    assert any(x['phase'] == 'candidate_backfill' for x in result.verification_audit)


def sentiment():
    return SentimentBundle(metrics=[SentimentMetric('CNN Fear & Greed', 43.14, 31.17, None),
        SentimentMetric('VIX', 15.52, 15.31, None), SentimentMetric('DXY', 102.10, 101.93, None),
        SentimentMetric('Shiller PE', 41.67, None, None), SentimentMetric('高收益债利差', 3.10, 3.24, None, unit='%')], fetched_at=NOW)


def test_sentiment_move_context_and_verified_computed_roles_are_not_false_rejections():
    bundle = sentiment()
    assert score_sentiment(bundle)['score'] == 58.3
    text = 'CNN 从31.17回升至43.14，VIX 由15.31微升至15.52，两者权重合计0.85；按当前水平计算，加权分为58.3，整体中性。'
    assert not _argument_errors(text, bundle, '中性')
    assert 'unsupported_weight' in _argument_errors(text.replace('0.85', '0.95'), bundle, '中性')
    assert 'unsupported_computed_number' in _argument_errors(text.replace('58.3', '68.3'), bundle, '中性')
    assert 'wrong_prior_value' in _argument_errors(text.replace('31.17', '31.27'), bundle, '中性')
    assert 'delta_cannot_explain_level_score' in _argument_errors('CNN 回升11.97，VIX 微升0.21，因此加权分为58.3，整体中性。', bundle, '中性')


def test_quality_distinguishes_user_provided_date_from_source_observation():
    row = {'key': 'MCO', 'status': 'carried', 'source_type': 'user_provided', 'provided_on': '2026-10-04', 'observed_at': None}
    output = quality_details({'observations': [row]})[0]
    assert '用户指定参考' in output and '2026-10-04' in output and '官方数据日期未核实' in output
    assert row['observed_at'] is None


def test_nested_source_watchdog_is_visible_and_reset_for_each_execution():
    reset_timeout_events()
    def timed_out():
        raise StageTimeout('valuation')
    result = RuntimeBudget().run('outer', lambda: RuntimeBudget().run('valuation', timed_out, seconds=1, fallback=lambda: 'carried'), seconds=2, fallback=lambda: 'missing')
    assert result == 'carried'
    assert timeout_events() == [{'stage': 'valuation', 'reason': 'timeout', 'limit_seconds': 1}]
    reset_timeout_events()
    assert not timeout_events()


def test_purpose_modality_is_preserved_not_confused_with_completed_action():
    from src.processors.translation_guard import translation_errors
    source = 'The operation aims to take back the port.'
    assert not translation_errors(source, '此次行动旨在夺回港口。')
    assert 'modality' in translation_errors(source, '此次行动已夺回港口。')


@pytest.mark.parametrize('source,translated', [
    ('Transport costs up to $40 million.', '运输成本最高达4000万美元。'),
    ('Insurance costs up to $20 million.', '保险成本最高达到2000万美元。'),
    ('High energy prices add to pressure on the euro.', '能源价格高企加大了对欧元的压力。'),
    ('Bond yields are rising.', '债券收益率在上升。'),
])
def test_macro_quantity_and_direction_equivalence_keeps_real_updates(source, translated):
    from src.processors.translation_guard import translation_errors
    assert not translation_errors(source, translated)
    if 'costs up to' in source:
        assert 'quantities_or_units' in translation_errors(source, translated.replace('最高达到', '').replace('最高达', ''))
    else:
        assert translation_errors(source, translated.replace('加大', '减少').replace('上升', '下降'))


@pytest.mark.parametrize('text', [
    'High energy prices and concerns over public finances add to pressure on the single currency.',
    'Oil prices add to pressure on the yen.',
    '能源价格高企加大了对欧元的压力。',
    '原油上涨加剧了对日元的压力。',
    'Euro slides to 17-month low against dollar.',
    'Sterling plunges after energy prices surge.',
])
def test_macro_classifies_affected_currency_not_background_energy(text):
    from src.processors.macro_topics import macro_topic
    assert macro_topic(text) == '外汇市场'
