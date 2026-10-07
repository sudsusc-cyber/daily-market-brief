"""Replay the actual October 7 source packets, including publication failures."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.collectors.company_news import NewsItem
from src.collectors.figures import FigureBundle, FigureMention
from src.collectors.macro_news import MacroNewsItem
from src.processors.editorial_evidence import analysis_source
from src.processors.figure_filter import filter_one
from src.processors.news_selection import complete_excerpt, factual_excerpt
from src.processors.source_grounding import grounded_text, source_sentences
from src.processors.speaker_attribution import attribution
from src.processors.thesis.extractor import _verified_grounding_row
from src.processors.thesis.renderer import _rule_for
from src.processors.translation_guard import translation_errors
from src.valuation.morningstar import SECURITIES, MorningstarPublicProvider

PACKET = json.loads((Path(__file__).parent / 'fixtures/october7_publication_sources.json').read_text())


def company(index):
    row = PACKET['company'][index]
    return NewsItem(row['original_title'], datetime.fromisoformat(row['published_at']),
                    row['url'], row['source_name'], summary=row['original_summary'])


def macro(index):
    row = PACKET['macro'][index]
    return MacroNewsItem(row['original_title'], datetime.fromisoformat(row['published_at']),
                         row['url'], row['source_name'], summary=row['original_summary'])


def test_paid_preview_cut_off_is_replaced_with_complete_apple_headline():
    item = company(1)
    assert not complete_excerpt(item.summary)
    assert not complete_excerpt(PACKET['company'][1]['validated_text'])
    assert factual_excerpt(item) == item.title
    assert item.summary not in source_sentences(item)


@pytest.mark.parametrize('text', ['The company reports revenue up.', '新芯片采用 2 nm。',
                               'OpenAI launches a new API.', '销售额增长 5%。'])
def test_complete_financial_sentences_and_units_survive(text):
    assert complete_excerpt(text)


@pytest.mark.parametrize('index', [3, 4])
def test_macro_retains_title_subject_amount_and_referential_context(index):
    item = macro(index)
    combined = item.title + '\n' + item.summary
    assert factual_excerpt(item) == combined
    assert source_sentences(item) == [combined]


def test_fundraising_translation_preserves_sense_amount_and_plan():
    original = PACKET['frontier'][0]['title']
    translated = 'OpenAI 寻求融资 300 亿美元，AI 筹资热潮加速 - Bloomberg.com'
    assert translation_errors(original, translated) == []
    assert translation_errors(original, translated.replace('300', '30'))
    assert translation_errors(original, translated.replace('寻求融资', '已融资'))
    assert translation_errors('OpenAI raises prices by 10%.', 'OpenAI 融资 10%。')


def test_analysis_gets_visible_attribution_and_replayable_evidence():
    item = company(2)
    assert analysis_source(item.title, item.summary)
    item.source_excerpt = factual_excerpt(item)
    item.translated_excerpt = PACKET['company'][2]['validated_text']
    text, rows = grounded_text(item.translated_excerpt, [item])
    assert text.startswith('作者分析：')
    assert rows[0]['source_kind'] == 'analysis'
    assert _verified_grounding_row(SimpleNamespace(text=text), rows[0])


def test_berkshire_equity_purchase_does_not_imply_partnership():
    item = company(4)
    row = dict(excerpt=factual_excerpt(item), output_text=PACKET['company'][4]['validated_text'])
    rule = _rule_for(row)
    assert rule.key == 'portfolio-allocation'
    assert '合作' not in rule.title + rule.watch


def voice():
    row = next(r for r in PACKET['voices'] if r['person'] == '黄仁勋' and 'Humanoid' in r['title'])
    item = FigureMention(row['title'], row['snippet'], datetime.fromisoformat(row['published_at']),
                         row['url'], row['source'])
    item.source_excerpt = row['source_excerpt']
    item.translated_excerpt = row['translated_excerpt']
    return item


def test_specific_reported_voice_survives_model_rejection_with_honest_date_label():
    item = voice()
    client = SimpleNamespace(chat=lambda *a, **kw: SimpleNamespace(
        text='▦ 1: no | score=3 | 普通行业观点', error=None))
    result = filter_one(FigureBundle('黄仁勋', 'query', 'Jensen Huang', [item]), client=client)
    assert result.items and result.items[0].score == 3
    assert '发言日期未独立确认' in result.items[0].date_note
    row = result.items[0].evidence[0]
    assert row['speaker_attribution']['date_basis'] == 'recent_reporting'
    assert _verified_grounding_row(result.items[0], row)
    assert 'verified_statement_recovery' in [r['phase'] for r in result.verification_audit]


def test_recovery_does_not_override_low_scored_factual_or_editorial_rejection():
    item = voice()
    client = SimpleNamespace(chat=lambda *a, **kw: SimpleNamespace(
        text='▦ 1: no | score=1 | 非本人近期发言', error=None))
    result = filter_one(FigureBundle('黄仁勋', 'query', 'Jensen Huang', [item]), client=client)
    assert not result.items


def test_wealth_projection_pitch_cannot_become_an_investor_voice():
    item = FigureMention('Warren Buffett Says Buy This Vanguard Index Fund -- It Could Turn $400 Per Month Into $820,000',
        'Investors can make a fortune by holding an index fund for decades.',
        datetime(2026,10,6,tzinfo=UTC), 'https://example.com/pitch', 'Yahoo')
    client = SimpleNamespace(chat=lambda *a, **kw: (_ for _ in ()).throw(AssertionError('promotion must be removed before model ranking')))
    assert not filter_one(FigureBundle('巴菲特','query','Warren Buffett',[item]),client=client).items
    from src.processors.news_selection import meaningful_quote

    item.title = 'Lisa Su says new chips can turn wasted energy into useful computation.'
    item.snippet = 'AMD announced a technical roadmap.'
    assert meaningful_quote(item)


def test_date_fallback_cannot_publish_known_old_statement_or_wrong_speaker():
    item = voice()
    item.source_published_at = '2026-08-31T00:00:00+00:00'
    assert not attribution(item, '黄仁勋', 'Jensen Huang', excerpt=item.title, allow_recent_reporting=True)
    item.source_published_at = ''
    assert not attribution(item, '奥特曼', 'Sam Altman', excerpt=item.title, allow_recent_reporting=True)
    item.source = 'Unknown Publisher'
    assert not attribution(item, '黄仁勋', 'Jensen Huang', excerpt=item.title, allow_recent_reporting=True)


def test_voice_label_uses_original_publisher_date_instead_of_rss_reposting():
    item = voice()
    item.source_published_at = '2026-10-04T12:00:00+08:00'
    client = SimpleNamespace(chat=lambda *a, **kw: SimpleNamespace(
        text='▦ 1: yes | score=4 | ' + item.source_excerpt, error=None))
    result = filter_one(FigureBundle('黄仁勋', 'query', 'Jensen Huang', [item]), client=client)
    assert result.items[0].date_note == '报道日期 10-04'


def test_voice_enrichment_rotates_people_within_the_request_budget(monkeypatch):
    from src.collectors import news_context

    calls = []
    monkeypatch.setattr(news_context, '_resolve', lambda url: url)
    monkeypatch.setattr(news_context, '_fetch', lambda url: calls.append(url) or b'<h1>unavailable</h1>')
    bundles = [FigureBundle(person, 'query', person, [FigureMention(
        f'{person} says AI chip demand is strong.', '', datetime.now(UTC),
        f'https://news.google.com/rss/articles/{person}{i}', 'CNBC') for i in range(3)])
        for person in ('Jane Smith', 'Alex Jones')]
    news_context.enrich_speaker_context(bundles, max_requests=2)
    assert len(calls) == 2 and 'Jane Smith' in calls[0] and 'Alex Jones' in calls[1]


def test_rate_limit_is_not_retried_during_send_reserve_but_expires(monkeypatch):
    provider = MorningstarPublicProvider(session=Mock(), secondary_provider=None)
    calls = []
    def failed(ticker, security, checked_at, values, failures, seconds):
        calls.append(ticker)
        failures[ticker] = 'new report returned HTTP 429'
    monkeypatch.setattr(provider, '_fetch_one', failed)
    now = datetime(2026, 10, 7, tzinfo=UTC)
    securities = {'COST': SECURITIES['COST']}
    provider.fetch_all(securities, checked_at=now)
    _, failures = provider.fetch_all(securities, checked_at=now + timedelta(minutes=6))
    assert calls == ['COST'] and '429' in failures['COST']
    provider.fetch_all(securities, checked_at=now + timedelta(minutes=16))
    assert calls == ['COST', 'COST']


def test_actual_intro_recovers_natural_signal_clause_without_transport_marker():
    from src.processors.holdings_intro import write_intro

    prose = '买入区间从不预告自己何时到来，它只是静静待在那里，等一个愿意按规则行事的人。腾讯控股与泡泡玛特现处定投的区间；真正稀缺的从来不是机会，而是机会出现时那份不被情绪挪动的心。'
    signal = '腾讯控股、泡泡玛特现处定投区间。'
    response = json.dumps({'text': prose, 'signal_text': signal}, ensure_ascii=False)
    client = SimpleNamespace(chat=lambda *a, **kw: SimpleNamespace(text=response))
    signals = [SimpleNamespace(signal='DCA', error=None, holding=SimpleNamespace(name=name))
               for name in ('腾讯控股', '泡泡玛特')]
    actual = write_intro(signals, client=client)
    assert actual and actual.startswith('买入区间从不') and signal.rstrip('。') in actual


def test_weekly_voice_window_recovers_unpublished_recent_speech(monkeypatch, tmp_path):
    from src.collectors import figure_official_sources, figures

    now = datetime(2026, 10, 7, tzinfo=UTC)
    monkeypatch.setattr(figures, 'last_24h_window', lambda: (now-timedelta(days=1), now))
    monkeypatch.setattr(figures, 'FIGURES', [('黄仁勋', 'query', 'en', 'Jensen Huang')])
    recent = FigureMention('Jensen Huang says chip demand remains strong.', '', now-timedelta(days=3), 'https://example.com/recent', 'Reuters')
    old = FigureMention('Jensen Huang says chip demand remains strong.', '', now-timedelta(days=8), 'https://example.com/old', 'Reuters')
    monkeypatch.setattr(figures, '_fetch_google_news', lambda *a, **kw: [recent, old])
    monkeypatch.setattr(figure_official_sources, 'fetch_all', lambda *a, **kw: {})
    bundles, _ = figures.fetch_all(state_path=tmp_path/'pushed.json')
    assert bundles[0].items == [recent]


def test_valuation_reports_are_not_displaced_by_newer_wire_promotions():
    from src.valuation.morningstar import _Candidate, _rank_candidates

    now = datetime(2026, 10, 7, tzinfo=UTC)
    report = _Candidate('https://www.morningstar.com/company-reports/current', now-timedelta(days=4))
    ad = _Candidate('https://www.morningstar.com/news/pr-newswire/new-promotion', now)
    assert _rank_candidates([ad, report]) == [report]
