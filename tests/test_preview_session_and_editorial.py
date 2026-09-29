"""Failures observed in production preview 36507985129."""
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest

from src.collectors import stocks
from src.processors.news_selection import company_candidate, factual_excerpt
from src.processors.source_grounding import grounded_text
from src.processors.thesis.renderer import _rule_for, build_judgment_section, validate_publication
from src.utils.market_clock import calendar, latest_closed_session


@pytest.mark.parametrize('symbol,now,expected', [
    ('0700.HK', datetime(2026, 9, 29, 1, 28, tzinfo=UTC), '2026-09-28'),
    ('9992.HK', datetime(2026, 9, 29, 2, tzinfo=UTC), '2026-09-28'),
    ('MSFT', datetime(2026, 9, 28, 15, tzinfo=UTC), '2026-09-25'),
    ('MSFT', datetime(2026, 11, 27, 17, tzinfo=UTC), '2026-11-25'),
    ('MSFT', datetime(2026, 11, 27, 19, tzinfo=UTC), '2026-11-27'),
    ('0700.HK', datetime(2026, 12, 24, 5, tzinfo=UTC), '2026-12-24'),
])
@pytest.mark.parametrize('interval,years', [('1d',2), ('1wk',5)])
def test_both_providers_bound_requests_to_closed_session(monkeypatch, symbol, now, expected, interval, years):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz or UTC)
    monkeypatch.setattr(stocks, 'datetime', Clock)
    ticker = SimpleNamespace(ticker=symbol, history=Mock(return_value=pd.DataFrame({'Close':[100]})))
    (stocks._yf_daily_history if interval == '1d' else stocks._yf_history)(ticker)
    kwargs = ticker.history.call_args.kwargs
    cal = calendar(symbol, now.year)
    end = pd.Timestamp(expected, tz=cal.tz) + pd.DateOffset(days=1)
    assert kwargs['end'] == end and kwargs['start'] == end - pd.DateOffset(years=years)
    assert kwargs['auto_adjust'] is False and kwargs['interval'] == interval
    calls=[]
    index = (pd.DatetimeIndex([pd.Timestamp(expected, tz=cal.tz)]) if interval == '1d'
             else pd.DatetimeIndex([pd.Timestamp(expected, tz=cal.tz) - timedelta(days=date.fromisoformat(expected).weekday())]))
    metadata={'symbol':symbol, 'currency':'HKD' if symbol.endswith('.HK') else 'USD',
              'exchangeName':'HKG' if symbol.endswith('.HK') else 'NMS'}
    result={'meta':metadata,'timestamp':[int(x.timestamp()) for x in index],
            'indicators':{'quote':[{'close':[100]}]}}
    def request(symbol, params):
        calls.append(params)
        return result
    monkeypatch.setattr(stocks, '_request_yahoo_chart', request)
    values,_=stocks._yahoo_chart_history(symbol, interval=interval, period=f'{years}y', minimum=1)
    assert values == [100]
    assert calls[0]['period2'] == int(end.timestamp())
    assert calls[0]['period1'] == int(kwargs['start'].timestamp())
    assert 'range' not in calls[0]


def test_intraday_row_still_rejected_if_server_ignores_requested_boundary(monkeypatch):
    now=datetime(2026,9,29,2,tzinfo=UTC)
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):
            return now
    monkeypatch.setattr(stocks,'datetime',Clock)
    hist=pd.DataFrame({'Close':[100,999]}, index=pd.to_datetime(['2026-09-28','2026-09-29']).tz_localize('Asia/Hong_Kong'))
    with pytest.raises(ValueError,match='过期或超前'):
        stocks._history_closes(hist,symbol='0700.HK',interval='1d')
    assert latest_closed_session('0700.HK',now) == date(2026,9,28)


def test_actual_berkshire_pitch_selects_complete_reported_purchase_only():
    title='If Berkshire’s $2.1 Billion Bet Is Right, You’ll Probably Wish You Bought This Unloved Housing Stock. Abel and Weschler Just Bought Another 2.7 Million Shares Of Lennar.'
    fact='Abel and Weschler Just Bought Another 2.7 Million Shares Of Lennar.'
    article=SimpleNamespace(title=title,summary='',url='https://example.com/berkshire')
    assert company_candidate(article,'BRK.B')
    assert factual_excerpt(article) == fact
    article.source_excerpt=fact
    article.translated_excerpt='Abel 和 Weschler 刚刚又买入了270万股 Lennar。'
    text,mapping=grounded_text(article.translated_excerpt,[article])
    assert text == article.translated_excerpt
    assert mapping[0]['original_title'] == title and mapping[0]['excerpt'] == fact
    assert not company_candidate(SimpleNamespace(title=title.split('. Abel')[0],summary=''),'BRK.B')


@pytest.mark.parametrize('text,key', [
    ('OpenAI 放弃发布即将推出的模型。','product-release-risk'),
    ('OpenAI 因安全问题搁置新模型。','product-release-risk'),
    ('Apple 推迟推出新手机。','product-release-risk'),
    ('OpenAI 发布新模型。','product-commercialization'),
    ('OpenAI 计划发布新模型。','product-commercialization'),
    ('OpenAI 没有取消发布新模型的计划。','product-commercialization'),
])
def test_thesis_watchpoint_matches_actual_release_state(text,key):
    row={'excerpt':text,'output_text':text}
    assert _rule_for(row).key == key


def test_actual_cancelled_release_cannot_keep_previous_commercialization_watchpoint():
    original='OpenAI abandons plan to release upcoming model as safety concerns escalate'
    text='安全担忧升级，OpenAI 放弃发布即将推出的模型'
    row=dict(original_title='Company confirms launch cancellation',original_summary=original,excerpt=original,output_text=text,
             validated_text=text,mode='checked_translation',url='https://example.com/cancel',published_at='2026-09-28')
    obj=SimpleNamespace(summary_html=text,evidence=[row],footnotes=[SimpleNamespace(url=row['url'])])
    sources={'macro':[obj]}
    result=build_judgment_section(sources=sources,today=date(2026,9,29))
    assert result.items[0]['theme']=='product-release-risk'
    assert result.items[0]['fact']==text
    assert result.items[0]['watch']=='调整原因、问题解决进度、后续发布安排与投入变化。'
    result.items[0]['thesis']='新产品的长期价值仍需持续采用和盈利兑现'
    assert validate_publication(result,sources=sources,today=date(2026,9,29)) is None


def test_identical_macro_frontier_fact_merges_sources_without_losing_changed_update():
    from src.processors.frontier_labs_filter import FrontierKeyPoint
    from src.processors.macro_filter import (
        MacroNewsSummary,
        _rebuild_safe_html,
        merge_frontier_duplicates,
    )
    original='OpenAI cancels the launch of its next model citing safety issues'
    chinese='OpenAI 以安全问题为由取消下一代模型的发布'
    def article(url, source, title, translation):
        return SimpleNamespace(title='Company product report',summary=title,url=url,source=source,published_at='2026-09-29',
                               source_excerpt=title,translated_excerpt=translation)
    base=article('https://example.com/macro','Financial Times',original,chinese)
    evidence=[]
    html,footnotes=_rebuild_safe_html('<p>AI。'+chinese+'[1]</p>',[base],evidence)
    macro=MacroNewsSummary(html,footnotes,evidence)
    def point(title, translation, url):
        text,mapping=grounded_text(translation,[article(url,'Financial Times',title,translation)])
        return FrontierKeyPoint('OpenAI',text,['MSFT'],url,'Financial Times',5,evidence=mapping)
    duplicate=point(original+' - Financial Times',chinese+' - Financial Times','https://example.com/frontier')
    update=point('OpenAI 发布新模型。','OpenAI 发布新模型。','https://example.com/update')
    merged,remaining,urls=merge_frontier_duplicates(macro,[duplicate,update])
    assert remaining==[update] and urls=={duplicate.source_url}
    assert merged.summary_html.count(chinese)==1
    assert {row.url for row in merged.footnotes}=={base.url,duplicate.source_url}
    assert len(merged.evidence)==2
    assert original+' - Financial Times' in [row['original_summary'] for row in merged.evidence]


def test_cross_section_same_subject_or_forged_mapping_cannot_erase_a_candidate():
    from src.processors.macro_filter import merge_frontier_duplicates
    fact='OpenAI 发布新模型。'
    row=dict(original_title=fact,original_summary='',excerpt=fact,output_text=fact,validated_text=fact,
             url='https://example.com/source',mode='verified_extract',published_at='2026-09-29')
    macro=SimpleNamespace(summary_html=fact,evidence=[row],footnotes=[SimpleNamespace(url=row['url'])])
    bad=SimpleNamespace(text=fact,evidence=[{**row,'original_title':'原文没有这个事实'}],source_url=row['url'])
    assert merge_frontier_duplicates(macro,[bad])==(macro,[bad],set())


@pytest.mark.parametrize('original,text,key', [
    ('OpenAI scraps rollout of new model over safety concerns',
     'OpenAI 因安全担忧取消新模型推出', 'product-release-risk'),
    ('OpenAI does not scrap rollout of new model',
     'OpenAI 没有取消推出新模型', 'product-commercialization'),
])
def test_scraps_rollout_preserves_actual_cancellation_state(original, text, key):
    assert _rule_for({'excerpt':original,'output_text':text}).key == key
