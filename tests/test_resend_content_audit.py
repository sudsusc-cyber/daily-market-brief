"""Sent-edition regressions plus counterexamples that preserve real news."""
import copy
import json
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from bs4 import BeautifulSoup

from src.collectors import sentiment
from src.collectors.company_news import NewsItem
from src.processors.editorial_evidence import editorial_issue, status_has_scope
from src.processors.macro_topics import macro_topic
from src.processors.news_presentation import publication_text
from src.processors.news_selection import company_candidate, factual_excerpt
from src.processors.news_summarizer import _rebuild_safe_summary
from src.processors.sentiment_judge import score_sentiment
from src.processors.source_grounding import grounded_text
from src.processors.thesis.renderer import build_judgment_section
from src.processors.translation_guard import translation_errors
from src.utils.last_good import LastGoodCache

FIXTURE=json.loads((Path(__file__).parent/'fixtures/september29_resend_audit.json').read_text())
NEWS=FIXTURE['36522465039']['news']
NOW=datetime(2026,9,29,4,40,tzinfo=UTC)


def bundle(run):
    return sentiment.SentimentBundle([
        sentiment.SentimentMetric(x['key'],x['current'],x['prior'],None,
            observed_at=x['observed_at'],stale_from=x['observed_at'] if x['status']=='carried' else None)
        for x in FIXTURE[run]['sentiment']],NOW)


def item(row, ticker=None):
    return SimpleNamespace(title=row['original_title'],summary=row['original_summary'],source=row['source_name'],
        url=row['url'],published_at=datetime.fromisoformat(row['published_at']),holding_ticker=ticker,
        source_excerpt=row['excerpt'],translated_excerpt=row['validated_text'])


def test_actual_resend_keeps_temperature_and_reports_transport_quality():
    old,new=bundle('36519910922'),bundle('36522465039')
    assert score_sentiment(old)['score']==score_sentiment(new)['score']==53.4
    before=score_sentiment(old)
    old.metrics[0].stale_from='2026-09-28'
    after=score_sentiment(old)
    assert after['score']==before['score'] and after['breakdown']==before['breakdown']
    assert after['coverage']['stale_metrics']==1


def test_cache_age_still_expires_and_reads_do_not_renew(tmp_path):
    cache=LastGoodCache(tmp_path)
    cache.put('sentiment.CNN',{'current':33.94,'observed_at':'2026-09-18','source':'CNN'},
              today=date(2026,9,18),observed_at='2026-09-18',source='CNN')
    def failed():
        return sentiment.SentimentMetric('CNN Fear & Greed',None,None,None,error='offline')
    for _ in range(2):
        m=sentiment._with_last_good(failed,cache=cache,cache_key='CNN',today=NOW.date())
        assert m.error and m.current is None
    assert cache.get('sentiment.CNN')[1]=='2026-09-18'


@pytest.mark.parametrize('clock,expected',[
    (datetime(2026,9,29,4,40,tzinfo=UTC),'2026-09-28'),
    (datetime(2026,9,29,20,59,tzinfo=UTC),'2026-09-28'),
    (datetime(2026,9,29,21,0,tzinfo=UTC),'2026-09-29'),
])
def test_dxy_excludes_only_unfinished_bar(clock,expected):
    result=sentiment._completed_dxy_values([100.97,101.2,101.266],['2026-09-25','2026-09-28','2026-09-29'],source='fixture',now=clock)
    assert result.observed_at==expected
    assert result[-1]==(101.2 if expected.endswith('28') else 101.266)
    if expected.endswith('28'):
        assert result[-2]==100.97


@pytest.mark.parametrize('clock,closed',[
    (datetime(2026,1,6,21,59,tzinfo=UTC),False),
    (datetime(2026,1,6,22,0,tzinfo=UTC),True),
    (datetime(2026,9,29,20,59,tzinfo=UTC),False),
    (datetime(2026,9,29,21,0,tzinfo=UTC),True),
])
def test_dxy_new_york_dst_boundary(clock,closed):
    assert sentiment._dxy_day_completed(clock.date(),clock)==closed


def test_dxy_weekend_holiday_and_invalid_completed_row():
    assert sentiment._completed_dxy_values([100,101],['2026-09-24','2026-09-25'],source='fixture',now=datetime(2026,9,27,tzinfo=UTC)).observed_at=='2026-09-25'
    # A source-supplied FX holiday gap is not validated with an equity calendar.
    assert sentiment._completed_dxy_values([100,101],['2026-09-24','2026-09-28'],source='fixture',now=NOW)[-1]==101
    with pytest.raises(ValueError,match='最新观测无效'):
        sentiment._completed_dxy_values([100,None,102],['2026-09-25','2026-09-28','2026-09-29'],source='fixture',now=NOW)
    with pytest.raises(ValueError,match='过期'):
        sentiment._completed_dxy_values([100],['2026-09-18'],source='fixture',now=NOW)
    with pytest.raises(ValueError,match='重复'):
        sentiment._completed_dxy_values([100,101],['2026-09-28','2026-09-28'],source='fixture',now=NOW)


@pytest.mark.parametrize('needle,ticker',[('Political Asset','NVDA'),('Analyst Blog','TSM'),('100 Years','MCO'),('80 Trillion','MA')])
def test_actual_editorial_noise_is_not_company_news(needle,ticker):
    row=next(x for x in NEWS['company'] if needle in x['original_title'])
    it=item(row,ticker)
    assert not company_candidate(it,ticker)
    assert grounded_text(row['validated_text'],[it])==('',[])


def test_old_financial_result_and_orphan_sentence_cannot_reenter_grounding():
    row=NEWS['company'][0]
    assert grounded_text(row['validated_text'],[item(row,'COST')])==('',[])
    row=next(x for x in NEWS['company'] if x['excerpt'].startswith('This launch'))
    it=item(row,'MA')
    assert editorial_issue(row['excerpt'])=='unresolved_context'
    assert factual_excerpt(it).startswith('In September 2026, Mastercard introduced Advanced B2B Analytics')
    assert not grounded_text(row['validated_text'],[it])[0]
    assert it.title==row['original_title']


def test_true_new_facts_remain_eligible_and_company_rows_are_grouped():
    items=[NewsItem(t,datetime(2026,9,29),f'https://example.com/{i}','Source',holding_ticker='MSFT') for i,t in enumerate([
        '微软发布新云产品。','微软营收增长10%。','微软营收增长20%。'])]
    raw='\n'.join(f'<strong>微软</strong>——{x.title}[{i}]' for i,x in enumerate(items,1))
    out=_rebuild_safe_summary(raw,items)
    assert out and len(BeautifulSoup(out.summary_html,'html.parser').select('div'))==1
    assert len(out.footnotes)==3 and '10%' in out.summary_html and '20%' in out.summary_html


def test_scope_required_and_translation_cannot_turn_research_pause_into_product_pause():
    row=NEWS['frontier'][1]
    assert grounded_text(row['validated_text'],[item(row)])==('',[])
    text='OpenAI paused training and evaluation involving tool use for its models.'
    good='OpenAI 暂停其模型涉及工具使用的训练和评估。'
    official=SimpleNamespace(title='An update',summary=text,url='https://openai.com/index/update/',source='OpenAI',
                             source_excerpt=text,translated_excerpt=good)
    before=copy.deepcopy(vars(official))
    assert status_has_scope(official,text)
    assert not translation_errors(text,good)
    assert grounded_text(good,[official])[0]==good
    assert translation_errors(text,'OpenAI 暂停其模型。')
    assert translation_errors(text,'OpenAI 取消其模型。')
    assert vars(official)==before
    fake=SimpleNamespace(title='OpenAI pauses GPT-6.1 Astra',summary='',url='https://openai.com.evil.example/x',source_type='official')
    assert not status_has_scope(fake,fake.title)
    # A concrete launch cancellation in a complete third-party body can pass.
    report='OpenAI announced it cancelled the launch of its new model after testing.'
    assert status_has_scope(SimpleNamespace(title='Model news',summary=report,url='https://example.com/report'),report)


def test_macro_action_and_figurative_meaning_are_preserved():
    assert macro_topic(NEWS['macro'][0]['output_text'])=='货币政策'
    assert macro_topic('Australia raises benchmark rates to fight inflation')=='货币政策'
    assert macro_topic('Australia CPI inflation rises to 3%')=='通胀数据'
    assert publication_text('Australia 上调基准利率。')=='澳大利亚上调基准利率。'
    source='Saudi Arabia resumes oil exports in blow to Iran'
    assert 'economic_impact_not_attack' in translation_errors(source,'Saudi Arabia 恢复石油出口，打击伊朗')
    assert not translation_errors(source,'Saudi Arabia 恢复石油出口，对伊朗构成打击')
    assert 'safety_gap_not_vulnerability' in translation_errors('OpenAI pauses training over safety gaps','OpenAI 因安全漏洞暂停训练')


def test_investment_commentary_does_not_supply_new_judgment_evidence():
    row=next(x for x in NEWS['company'] if 'visibility' in x['excerpt'])
    obj=SimpleNamespace(summary_html=row['output_text'],evidence=[row],footnotes=[SimpleNamespace(url=row['url'])])
    assert build_judgment_section(sources={'company_news':[obj]},today=NOW.date()) is None
    row=next(x for x in NEWS['company'] if 'Crusoe' in x['excerpt'])
    text,rows=grounded_text(row['validated_text'],[item(row,'GOOG')])
    obj=SimpleNamespace(summary_html=text,evidence=rows,footnotes=[SimpleNamespace(url=row['url'])])
    result=build_judgment_section(sources={'company_news':[obj]},today=NOW.date())
    assert result and result.items[0]['fact']==text


def test_fallback_keeps_explicit_audit_path_even_with_checked_translation():
    row=NEWS['company'][1]
    text,rows=grounded_text('模型自由添加的总结',[item(row,'COST')])
    assert text and rows[0]['mode']=='checked_translation'
    assert rows[0]['publication_path']=='source_fallback'


@pytest.mark.parametrize('path',['yfinance','chart'])
def test_both_dxy_adapters_select_the_same_completed_observation(monkeypatch,path):
    import pandas as pd
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):
            return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)
    monkeypatch.setattr(sentiment,'datetime',Clock)
    if path=='yfinance':
        history=pd.DataFrame({'Close':[100.97,101.2,101.266]},index=pd.to_datetime(['2026-09-25','2026-09-28','2026-09-29']).tz_localize('America/New_York'))
        monkeypatch.setattr(sentiment.yf,'Ticker',lambda *_:SimpleNamespace(history=lambda **_:history))
        values=sentiment._fetch_yfinance_close('DX-Y.NYB')
    else:
        stamps=[int(datetime(2026,9,d,4,tzinfo=UTC).timestamp()) for d in [25,28,29]]
        data={'chart':{'result':[{'timestamp':stamps,'indicators':{'quote':[{'close':[100.97,101.2,101.266]}]}}]}}
        monkeypatch.setattr(sentiment.requests,'get',lambda *a,**k:SimpleNamespace(status_code=200,json=lambda:data))
        values=sentiment._fetch_yahoo_chart_close('DX-Y.NYB')
    assert list(values)==[100.97,101.2] and values.observed_at=='2026-09-28'


def test_a_rejected_row_is_recorded_while_valid_news_survives():
    bad=next(x for x in NEWS['company'] if 'Analyst Blog' in x['original_title'])
    old=item(bad,'TSM')
    old.related_holding_tickers=[]
    good=NewsItem('微软发布新云产品。',NOW,'https://example.com/fact','Source',holding_ticker='MSFT')
    out=_rebuild_safe_summary('<strong>台积电</strong>——'+bad['output_text']+'[1]\n<strong>微软</strong>——微软发布新云产品。[2]',[old,good])
    assert out and len(out.content_rejections)==1
    assert '台积电' not in out.summary_html and '微软' in out.summary_html
