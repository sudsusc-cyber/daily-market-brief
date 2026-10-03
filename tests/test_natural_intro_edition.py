import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from src.collectors.sentiment import SentimentBundle, SentimentMetric
from src.processors.holdings_intro import write_intro
from src.processors.macro_topics import macro_topic
from src.processors.news_presentation import present, replay_presentation
from src.processors.news_selection import company_candidate
from src.processors.sentiment_judge import _argument_errors

PROSE = '理解一门生意，需要把热闹留在窗外；{信号背景}，并不妨碍我们继续耐心打磨判断，让事先写下的规则替临场的情绪作决定，把时间留给真正值得等待的价值。'


def client(text):
    return SimpleNamespace(chat=lambda *a, **kw: SimpleNamespace(text=text))


def test_natural_intro_does_not_depend_on_signal_state():
    prose = PROSE.replace('；{信号背景}，', '，')
    for kind in ('NONE', 'DCA', 'LUMP_SUM'):
        text = write_intro([SimpleNamespace(signal=kind, error=None)], client=client(json.dumps({'text': prose})))
        assert text == prose and '持仓' not in text


@pytest.mark.parametrize('raw', [
    '{信号背景}不要让热闹替代思考。',
    PROSE.replace('{信号背景}', '并非{信号背景}'),
    PROSE.replace('{信号背景}', '{信号背景}{信号背景}'),
    PROSE.replace('并不妨碍', '其余标的均在参考线之上，并不妨碍'),
])
def test_intro_cannot_reintroduce_fixed_lead_or_reverse_facts(raw):
    assert write_intro([SimpleNamespace(signal='DCA', error=None)], client=client(raw)) is None


@pytest.mark.parametrize('text,expected', [
    ('Gold Steady as Softer Inflation Data Tempers Fed Rate-Hike Bets', '贵金属市场'),
    ('Silver rises as traders await Fed interest-rate decisions', '贵金属市场'),
    ('黄金持稳，通胀数据走软抑制了美联储加息押注。', '贵金属市场'),
    ('Thai bonds suffered foreign outflows as surging Treasury yields dimmed their appeal.', '债券市场'),
    ('French bonds fall as US Treasury yields rise', '债券市场'),
    ('美国国债收益率上涨，投资者关注泰国债市。', '美债市场'),
])
def test_macro_subject_beats_causal_background(text, expected):
    assert macro_topic(text) == expected


@pytest.mark.parametrize('source', ['The Washington Post', 'Unknown Daily', '新日报'])
def test_collapsed_publisher_tail_requires_original_metadata(source):
    original = 'FTC launches broad investigation into Acme  ' + source
    translation = 'FTC 对 Acme 展开广泛调查 ' + source
    output = present(translation, source_name=source, original_text=original)
    assert output.text == 'FTC 对 Acme 展开广泛调查'
    row = {'presentation_version':output.version, 'source_name':source, 'excerpt':original}
    assert replay_presentation(translation, row) == output.text
    legitimate = 'Acme 与 ' + source + ' 合作'
    assert source in present(legitimate, source_name=source, original_text=original).text
    original_double = f'Acme partners with {source} - {source}'
    translated_once = 'Acme 与 ' + source
    assert source in present(translated_once, source_name=source, original_text=original_double).text


def test_sentiment_distinguishes_change_to_level_from_change_by_amount():
    bundle = SentimentBundle([
        SentimentMetric('CNN Fear & Greed',30.83,31.63,None),
        SentimentMetric('VIX',16.34,16.04,None),
        SentimentMetric('DXY',101.56,101.37,None),
        SentimentMetric('高收益债利差',3.08,3.02,None,unit='%'),
    ],datetime.now(UTC))
    assert not _argument_errors('CNN 从31.63降至30.83、VIX 由16.04微升至16.34，整体中性。',bundle,'中性')
    assert not _argument_errors('高收益债利差从3.02%走阔至3.08%、DXY 从101.37升至101.56，整体中性。',bundle,'中性')
    assert 'wrong_prior_value' in _argument_errors('VIX 从16.34升至16.04。',bundle,'中性')
    assert 'wrong_current_value' in _argument_errors('VIX 从16.34升至16.04。',bundle,'中性')


def test_actual_formal_edition_opinions_are_not_company_developments():
    import json
    from pathlib import Path

    rows=json.loads((Path(__file__).parent/'fixtures/october1_formal_evidence.json').read_text())['sections']
    for idx,ticker in [(0,'MSFT'),(1,'COST'),(4,'GOOG'),(6,'KO')]:
        r=rows['company'][idx]
        item=SimpleNamespace(title=r['original_title'],summary=r['original_summary'],source=r['source_name'],url=r['url'])
        assert not company_candidate(item,ticker)
    r=rows['frontier'][1]
    assert 'The Washington Post' not in present(r['validated_text'],source_name=r['source_name'],original_text=r['excerpt']).text
    for r in rows['company']+rows['macro']+rows['frontier']+rows['figures']:
        assert replay_presentation(r['validated_text'],r)==r['output_text']


def test_actual_operating_event_survives_valuation_commentary_title():
    item=SimpleNamespace(title='Acme could be 20% above fair value',summary='Acme reported revenue of $5 billion.',source='',url='https://example.com')
    assert company_candidate(item,'MSFT')


@pytest.mark.parametrize('issuer', ['Berkshire Hathaway', 'Acme', 'Northstar'])
def test_immediate_succession_needs_fresh_event_date_not_feed_timestamp(issuer):
    from src.processors.news_selection import factual_excerpt, undated_immediate_leadership_change

    body=f'{issuer} announced its chairman stepped down, effective immediately.'
    item=SimpleNamespace(title=f'{issuer} appoints a new chairman',summary=body,source='News',url='https://example.com/news',published_at=datetime(2026,9,30,tzinfo=UTC))
    assert undated_immediate_leadership_change(item,body)
    assert not factual_excerpt(item)
    for event_date, rejected in [('September 18, 2026',True),('September 30, 2026',False)]:
        item.summary=body.replace('announced',f'announced on {event_date}')
        assert undated_immediate_leadership_change(item,item.summary) is rejected


def test_model_failure_fallback_also_varies_and_keeps_actual_signal_state():
    from src.processors.holdings_intro import fallback_intro

    signals=[SimpleNamespace(signal='NONE',error=None)]
    texts=[fallback_intro(signals,datetime(2026,10,1,tzinfo=UTC)+timedelta(days=i)) for i in range(7)]
    assert len(set(texts))==7
    assert all('信号' not in t and '买入区间' not in t for t in texts)
    assert all(not t.startswith('持仓') and '{' not in t for t in texts)
