"""Observed October 1 failures plus different issuers, dates and units."""
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.processors.holdings_intro import write_intro
from src.processors.news_presentation import present, replay_presentation
from src.processors.news_selection import company_candidate, factual_excerpt, old_event_excerpt
from src.processors.source_grounding import grounded_text, source_sentences
from src.processors.thesis.renderer import _publication_item
from src.processors.translation_guard import translation_errors

NOW = datetime(2026, 9, 30, tzinfo=UTC)


def article(title, summary='', ticker=None):
    return SimpleNamespace(title=title, summary=summary, holding_ticker=ticker,
                           published_at=NOW, url='https://example.com/source', source='Source')


@pytest.mark.parametrize('company', ['Mastercard', 'Acme', '新星科技'])
@pytest.mark.parametrize('fact', [
    '{company} completed the acquisition on August 3, 2026.',
    '{company} 于 2026 年 8 月 3 日完成了收购。',
    'On August 3, 2026, {company} signed the agreement.',
    '{company} launched the platform on 2026-08-03.',
])
def test_old_event_any_issuer_or_date_position(company, fact):
    text = fact.format(company=company)
    item = article(text, text)
    assert old_event_excerpt(item, text)
    assert factual_excerpt(item) == ''
    assert grounded_text(text, [item]) == ('', [])
    result, reason = _publication_item('company', dict(published_at=NOW.isoformat(), excerpt=text), NOW.date())
    assert result is None and reason == 'old_event_not_new_evidence'


@pytest.mark.parametrize('text', [
    'Acme reported its best sales since August 3, 2020.',
    'Acme reported results for the quarter ended August 3, 2026.',
    'Acme announced the acquisition on September 29, 2026.',
    'Acme will launch the product on October 3, 2026.',
    'Acme 公布自 2020 年 8 月 3 日以来最好的营收。',
])
def test_dates_do_not_erase_new_events_comparisons_or_future_plans(text):
    assert not old_event_excerpt(article(text), text)


def test_old_first_event_cannot_hide_explicit_new_progress():
    item = article('Acme updates its business',
                   'Acme completed the acquisition on August 3, 2026. Acme announced a new contract today.')
    assert factual_excerpt(item) == 'Acme announced a new contract today.'


@pytest.mark.parametrize('ticker, issuer', [('NVDA', 'NVIDIA'), ('MSFT', 'Microsoft'), ('MA', 'Mastercard')])
def test_partner_excerpt_keeps_holding_participation(ticker, issuer):
    first = 'Acme Cloud announced $660 million in new financing.'
    second = f'The round included participation from {issuer}.'
    item = article('Acme Cloud raises financing', first + ' ' + second, ticker)
    assert factual_excerpt(item) == item.summary
    assert first not in source_sentences(item)
    item.summary = first + ' Another company participated.'
    assert factual_excerpt(item) == ''


def test_old_translation_that_drops_holding_is_not_reused():
    first = 'Acme Cloud announced $660 million in financing.'
    item = article('Acme financing', first + ' NVIDIA participated.', 'NVDA')
    item.source_excerpt, item.translated_excerpt = first, 'Acme Cloud 宣布获得 6.6 亿美元融资。'
    assert grounded_text(item.translated_excerpt, [item]) == ('', [])


@pytest.mark.parametrize('issuer,ticker', [('Microsoft', 'MSFT'), ('NVIDIA', 'NVDA')])
def test_best_quarter_price_colour_requires_business_fact(issuer, ticker):
    item = article(f'{issuer} Stock Is Having Its Best Quarter Since 1991', ticker=ticker)
    assert not company_candidate(item, ticker)
    item.summary = f'{issuer} announced a new $10 billion buyback today.'
    assert company_candidate(item, ticker)


def test_structured_intro_owns_signal_clause_and_checks_frame():
    prose = '投资的尺度，需要在喧哗之外慢慢建立；真正值得反复打磨的是对生意的理解，把规则写在情绪之前，让耐心陪伴价值兑现，也为未知保留应有的位置。'
    client = Mock()
    client.chat.return_value = SimpleNamespace(text=json.dumps({'text': prose}, ensure_ascii=False))
    signals = [SimpleNamespace(error=None, signal='DCA'), SimpleNamespace(error=None, signal='NONE')]
    assert write_intro(signals, client=client) == prose
    client.chat.return_value.text = json.dumps({'text': prose + '其余标的全部上涨，建议立即买入。'}, ensure_ascii=False)
    assert write_intro(signals, client=client) is None


@pytest.mark.parametrize('raw,expected', [('$150B', '1500亿美元'), ('$235B', '2350亿美元'),
    ('$1.25B', '12.5亿美元'), ('HK$2.5M', '250万港元'), ('€0.5T', '5000亿欧元')])
def test_currency_conversion_is_exact_and_replay_versioned(raw, expected):
    assert present(f'公司获批 {raw} 回购。').text == f'公司获批 {expected} 回购。'
    assert replay_presentation(raw, {'presentation_version': 7}) == raw


def test_navigation_removed_without_deleting_news_fact():
    raw = '今日股市，9月30日：Nvidia 批准 $150B 回购。'
    assert present(raw).text == 'Nvidia 批准 1500亿美元 回购。'


@pytest.mark.parametrize('noun,zh', [('economies', '经济体'), ('banks', '银行'), ('companies', '公司')])
def test_plural_superlatives_remain_plural(noun, zh):
    source = f"The largest {noun} reported revenue."
    assert 'plural_subject_scope:' + noun in translation_errors(source, f'最大的{zh}公布营收。')
    assert 'plural_subject_scope:' + noun not in translation_errors(source, f'几家最大的{zh}公布营收。')


def test_received_email_evidence_replays_and_identified_failures_are_blocked():
    from pathlib import Path

    rows = json.loads((Path(__file__).parent / 'fixtures/october1_received_sources.json').read_text())
    for section in rows.values():
        for row in section:
            assert replay_presentation(row['validated_text'], row) == row['output_text']
    def source(row, ticker):
        item = article(row['original_title'], row['original_summary'], ticker)
        item.published_at = row['published_at']
        item.source_excerpt = row['excerpt']
        item.translated_excerpt = row['validated_text']
        return item
    assert not company_candidate(source(rows['company'][0], 'MSFT'), 'MSFT')
    gmi = source(rows['company'][2], 'NVDA')
    assert 'with participation from NVIDIA' in factual_excerpt(gmi)
    assert grounded_text(gmi.translated_excerpt, [gmi]) == ('', [])
    gmi.source_excerpt = factual_excerpt(gmi)
    gmi.translated_excerpt += ' B 轮融资由 ARCHIV 领投，该公司是一家位于 San Francisco、专注于 AI 和机器人领域的新投资公司，NVIDIA 参与投资。'
    published, mapping = grounded_text(gmi.translated_excerpt, [gmi])
    assert 'NVIDIA 参与投资' in published and mapping[0]['excerpt'] == gmi.source_excerpt
    master = next(r for r in rows['company'] if 'BVNK' in r['original_summary'])
    assert factual_excerpt(source(master, 'MA')) == ''
    assert _publication_item('company', master, NOW.date())[1] == 'old_event_not_new_evidence'
    buyback = present(rows['company'][1]['validated_text']).text
    assert '今日股市' not in buyback and '$' not in buyback and '1500亿美元' in buyback
    inflation = rows['macro'][1]
    assert 'plural_subject_scope:economies' in translation_errors(inflation['excerpt'], inflation['validated_text'])


def test_temperature_uses_current_levels_not_size_of_moves():
    from src.collectors.sentiment import SentimentBundle, SentimentMetric
    from src.processors.sentiment_judge import (
        _argument_errors,
        _deterministic_argument,
        _format_input,
        score_sentiment,
    )

    bundle = SentimentBundle([
        SentimentMetric('CNN Fear & Greed', 30.83, 31.63, None),
        SentimentMetric('VIX', 16.34, 16.04, None),
        SentimentMetric('DXY', 101.59, 101.37, None),
        SentimentMetric('Shiller PE', 41, 41, None),
        SentimentMetric('高收益债利差', 3.08, 3.02, None, unit='%'),
    ], NOW)
    result = score_sentiment(bundle)
    assert result['score'] == 51.2
    bad = 'CNN 恐惧贪婪指数从31.63微降至30.83，VIX 由16.04升至16.34，两者方向一致但幅度有限，故加权分落在中性档位。'
    assert 'delta_cannot_explain_level_score' in _argument_errors(bad, bundle, '中性')
    argument = _deterministic_argument(bundle, '中性')
    assert '30.83当前水平对应偏冷' in argument and '16.34当前水平对应偏热' in argument
    prompt = _format_input(bundle, '中性', result['score'])
    assert '情绪映射分 68.3' in prompt and '有效权重 0.40' in prompt
    # The exact same levels with opposite moves still give the same score and
    # the same explanation of the current score (not merely the same verdict).
    for metric in bundle.metrics:
        metric.prior = metric.current * 2
    assert score_sentiment(bundle)['score'] == 51.2
    assert _deterministic_argument(bundle, '中性') == argument


def test_explicit_new_calendar_update_survives_old_background():
    old = 'Acme completed the acquisition on August 3, 2026.'
    new = 'Acme announced a new contract on September 30, 2026.'
    item = article('Acme update', old + ' ' + new)
    assert factual_excerpt(item) == new
    # A combined passage does not let an old event trigger a new thesis label.
    row = dict(published_at=NOW.isoformat(), excerpt=item.summary)
    assert _publication_item('company', row, NOW.date())[1] == 'old_event_not_new_evidence'
    assert old_event_excerpt(item, 'Today Acme reported it completed the acquisition on August 3, 2026.')


def test_adjacent_unrelated_sentence_cannot_rescue_partner_financing():
    item = article('Acme funding', 'Acme announced new financing. NVIDIA reported lower revenue.', 'NVDA')
    assert 'Acme' not in factual_excerpt(item)


def test_production_translation_binds_holding_before_selecting_excerpt(monkeypatch):
    import src.main as main
    from src.collectors.company_news import CompanyNewsBundle, NewsItem
    from src.config import HOLDINGS

    source = NewsItem('Acme financing', NOW, 'https://example.com/funding', 'Source',
                      summary='Acme announced new financing. The round included participation from NVIDIA.')
    bundle = CompanyNewsBundle(next(h for h in HOLDINGS if h.ticker == 'NVDA'), [source])
    selected = []
    monkeypatch.setattr('src.collectors.news_context.enrich_technical_context', lambda rows: None)
    monkeypatch.setattr(main.translator, 'translate_in_place_news',
                        lambda rows, **kw: selected.extend(factual_excerpt(i) for i in rows))
    main._translate_all_bundles(cn_bundles=[bundle], fig_bundles=[], macro_bundles=[], client=Mock())
    assert selected == [source.summary]
    assert source.holding_ticker == 'NVDA'
