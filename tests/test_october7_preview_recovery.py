"""Regressions from the first real production-path recovery preview."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest

from src.collectors.figures import FigureMention
from src.collectors.macro_news import MacroNewsItem
from src.processors.macro_topics import macro_topic
from src.processors.news_selection import chinese_prose, factual_excerpt
from src.processors.translation_guard import translation_errors
from src.valuation.models import ValuationDisplay
from src.valuation.morningstar import (
    SECURITIES,
    MorningstarPublicProvider,
    load_cache,
    refresh_fair_values,
)
from src.valuation.service import apply_morningstar_fair_values

PACKET = json.loads((Path(__file__).parent/'fixtures/october7_preview_sources.json').read_text())
BASELINE = Path(__file__).parent/'fixtures/october7_verified_msft.json'
NOW = datetime(2026, 10, 7, tzinfo=UTC)


def test_actual_lisa_su_statement_survives_truncated_listing_metadata():
    row = next(r for r in PACKET['figures'] if r['presentation_speaker']=='苏妈')
    item = FigureMention(row['original_title'], row['original_summary'],
                         datetime.fromisoformat(row['published_at']), row['url'], row['source_name'])
    selected = factual_excerpt(item)
    assert selected == 'AMD CEO Lisa Su Says AI Demand Will Stay ‘Very, Very High’ for Years'
    assert 'NASDAQ' not in selected


def test_listing_syntax_cannot_erase_a_substantive_second_clause():
    from src.processors.news_selection import editorial_headline_excerpt

    title = 'Jane Smith says demand is high - AMD raises forecasts (NASDAQ:AMD)'
    assert not editorial_headline_excerpt(title)


def test_actual_mixed_language_macro_sentence_requires_translation():
    row = PACKET['macro'][-1]
    assert not chinese_prose(row['output_text'])
    assert 'not_chinese' in translation_errors(row['excerpt'], row['validated_text'])
    assert not translation_errors(row['excerpt'], '美国国债下跌，油价攀升。')
    assert chinese_prose('Nvidia 与 Hyundai 深化机器人领域合作。')


def test_rolling_page_date_is_not_an_extra_macro_fact():
    row = PACKET['macro'][1]
    item = MacroNewsItem(row['original_title'], datetime.fromisoformat(row['published_at']),
                         row['url'], row['source_name'], summary=row['original_summary'])
    assert factual_excerpt(item) == item.summary


def test_russian_public_health_story_is_not_a_ukraine_war_story():
    row = PACKET['macro'][2]
    assert macro_topic(row['original_title']+' '+row['original_summary']) == '公共卫生'
    assert macro_topic('Russian troops attack Ukraine.') == '俄乌局势'


@pytest.mark.parametrize('official_date,is_new', [('2025-07-31',False), ('2026-09-22',False), ('2026-09-28',True)])
def test_cached_estimate_does_not_call_older_official_date_a_new_amount(tmp_path, official_date, is_new):
    provider = Mock()
    provider.fetch_all.return_value = ({}, {'MSFT':'官方页面日期 '+official_date+'；标的取数超时'})
    provider.discovery_diagnostics = {'MSFT':{'official_fair_value_date':official_date}}
    values, failures = refresh_fair_values(provider=provider, state_dir=tmp_path,
        prices={}, checked_at=NOW, baseline_path=BASELINE)
    display = apply_morningstar_fair_values({'MSFT':ValuationDisplay(ticker='MSFT',status='not_due')},
        fair_values=values, failures=failures, prices={'MSFT':529.3})['MSFT']
    assert ('新金额' in display.data_note) == is_new


def quote(date='Sep 22, 2026', analysis='Sep 25, 2026'):
    text = f'''Title: Microsoft Stock Price | Morningstar
## Company Report
### [Microsoft analyst update](http://www.morningstar.com/stocks/xnas/msft/analysis)
Analyst {analysis}
## Price vs Fair Value
Fair Value
LOCK|abc
{date}
'''
    return Mock(text=text)


def test_unchanged_verified_estimate_checks_metadata_without_rereading_amount(monkeypatch):
    old = load_cache(BASELINE)['MSFT']
    provider = MorningstarPublicProvider(session=Mock(), secondary_provider=None)
    provider.seed_verified_history({'MSFT':old})
    monkeypatch.setattr(provider,'_reader_get',lambda _:quote())
    monkeypatch.setattr(provider,'_discover',lambda _:(_ for _ in ()).throw(AssertionError('unneeded report download')))
    values, failures = provider.fetch_all({'MSFT':SECURITIES['MSFT']},checked_at=NOW)
    assert not failures
    assert values['MSFT'].fair_value == old.fair_value
    assert values['MSFT'].retrieved_at == old.retrieved_at
    assert values['MSFT'].source_url == old.source_url
    assert provider.discovery_diagnostics['MSFT']['value_read']=='unchanged_verified_history'


@pytest.mark.parametrize('response', [quote('Oct 6, 2026'),quote(analysis='Oct 6, 2026'),quote('not available')])
def test_new_or_unknown_update_metadata_still_requires_real_amount_read(monkeypatch, response):
    provider = MorningstarPublicProvider(session=Mock(), secondary_provider=None)
    provider.seed_verified_history({'MSFT':load_cache(BASELINE)['MSFT']})
    monkeypatch.setattr(provider,'_reader_get',lambda _:response)
    assert provider._unchanged_history(SECURITIES['MSFT'],NOW) is None


def test_outer_timeout_snapshot_is_also_cooled_down_before_final_check(monkeypatch):
    provider = MorningstarPublicProvider(session=Mock(), secondary_provider=None)
    first = True
    calls = []
    def run(name, operation, **kwargs):
        nonlocal first
        if name == 'morningstar' and first:
            first = False
            provider._inflight_failures['COST'] = '公开估值取数超时；来源暂不可读'
            return kwargs['fallback']()
        return operation()
    monkeypatch.setattr(provider._budget,'run',run)
    def attempt(ticker, security, checked_at, values, failures, seconds):
        calls.append(ticker)
        failures[ticker] = '公开估值取数超时'
    monkeypatch.setattr(provider,'_fetch_one',attempt)
    securities = {'COST':SECURITIES['COST']}
    provider.fetch_all(securities,checked_at=NOW)
    _, errors = provider.fetch_all(securities,checked_at=NOW+timedelta(minutes=6))
    assert not calls and '来源暂不可读' in errors['COST']
    provider.fetch_all(securities,checked_at=NOW+timedelta(minutes=16))
    assert calls == ['COST']
