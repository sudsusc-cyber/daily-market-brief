"""The user's macro layout: one paragraph per topic in every successful path."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from bs4 import BeautifulSoup

from src.collectors.macro_news import MacroFeedBundle, MacroNewsItem
from src.processors.macro_filter import _rebuild_safe_html, summarize
from src.processors.macro_topics import macro_topic
from src.renderer.render import render_email

NOW = datetime(2026, 9, 29, tzinfo=UTC)
# Editorial grouping fixtures derived from the user's requested layout, not a
# claim that these reports have been independently verified against live sources.
FACTS = [
    'Xi Jinping 抵达美国与 Donald Trump 举行峰会，讨论贸易与科技分歧。',
    '美国与盟友之间的紧张关系令其对华施压空间收窄。',
    '中国推进自给自足的战略正在改变谈判盘算。',
    '美债收益率出现关税冲击以来最大涨幅。',
    '亚洲股市与债市承压，美国国债收益率上行加剧通胀担忧。',
    '市场关注美债收益率快速上行对经济的影响。',
    '多国取消飞往伊朗的航班。',
    'IAEA 总干事 Grossi 表示，对伊朗核材料的核查可以快速恢复。',
    '油价在中东供应风险持续背景下反弹。',
]


def _items():
    return [MacroNewsItem(text, NOW, f'https://example.com/{i}', f'Source {i}') for i,text in enumerate(FACTS)]


def _assert_layout(html, notes, evidence):
    soup = BeautifulSoup(html, 'html.parser')
    paragraphs = soup.select('p')
    assert [p.select_one('span').get_text() for p in paragraphs] == ['中美关系。', '美债市场。', '中东局势。']
    assert len(notes) == len(evidence) == 9
    for i,p in enumerate(paragraphs):
        facts = p.select('[data-macro-fact]')
        assert [f.get_text() for f in facts] == FACTS[i*3:i*3+3]
        assert all(not f.select('a') for f in facts)  # all citations at paragraph end
        assert [a['href'] for a in p.select('a')] == [f'https://example.com/{j}' for j in range(i*3,i*3+3)]
        assert p.contents[-1].name == 'sup'
    assert {r['macro_topic'] for r in evidence} == {'中美关系', '美债市场', '中东局势'}
    assert {r['output_text'] for r in evidence} == set(FACTS)


@pytest.mark.parametrize('raw', [
    '<p>宏观动态。任意模型拼接' + ''.join(f'[{i}]' for i in range(1,10)) + '</p>',
    ''.join(f'<p>不同标题。无依据的模型改写[{i}]</p>' for i in range(1,10)),
    '\n\n'.join(f'换一种标签。无依据的模型改写[{i}]' for i in range(1,10)),
])
def test_user_example_always_coalesces_into_three_topics(raw):
    evidence = []
    html, notes = _rebuild_safe_html(raw, _items(), evidence)
    _assert_layout(html, notes, evidence)


def test_nonadjacent_sources_and_changed_facts_are_never_split_or_deleted():
    items = _items()
    items[1].title = '美国与中国讨论降低25基点利率。'
    items[2].title = '美国与中国讨论降低50基点利率。'
    raw = ''.join(f'<p>宏观动态。混排[{i}]</p>' for i in [1,4,7,2,5,8,3,6,9])
    html, notes = _rebuild_safe_html(raw, items)
    soup = BeautifulSoup(html, 'html.parser')
    assert len(soup.select('p')) == 3
    assert '25基点' in soup.select('p')[0].get_text() and '50基点' in soup.select('p')[0].get_text()
    assert len(notes) == 9


@pytest.mark.parametrize('text,expected', [
    ('Xi Jinping arrives in US for summit with Donald Trump on trade and technology', '中美关系'),
    ('中美科技与关税磋商继续', '中美关系'),
    ('China and America discuss technology', '中美关系'),
    ('Treasury yields soar on inflation and tariff concerns', '美债市场'),
    ('Asian bonds follow Treasuries lower as Iran tensions lift oil prices', '美债市场'),
    ('美债收益率因油价上涨而上行', '美债市场'),
    ('Oil rebounds as Iran talks and Saudi pipeline risks persist', '中东局势'),
    ('IAEA considers inspections of Iran nuclear materials', '中东局势'),
    ('多国暂停飞往伊朗的航班', '中东局势'),
    ('欧洲央行维持政策利率', '货币政策'),
    ('未知类别的第一条事实', '其他宏观'),
])
def test_specific_topics_take_priority_over_incidental_broad_keywords(text, expected):
    assert macro_topic(text) == expected


def test_retries_and_final_renderer_keep_the_same_grouping_contract():
    items = _items()
    # Collector windows remain unchanged (eight per feed); topic grouping spans feeds.
    bundles = [MacroFeedBundle('A', items[:5]), MacroFeedBundle('B', items[5:])]
    responses = iter([SimpleNamespace(text='', error='timeout'), SimpleNamespace(
        text=''.join(f'<p>宏观动态。乱写[{i}]</p>' for i in range(1,10)), error=None)])
    calls = []
    def chat(*args, **kwargs):
        calls.append(kwargs)
        return next(responses)
    result = summarize(bundles, client=SimpleNamespace(chat=chat))
    assert len(calls) == 2 and result is not None
    _assert_layout(result.summary_html, result.footnotes, result.evidence)
    rendered = render_email(signals=[], generated_at=NOW, macro_news_summary=result)
    soup = BeautifulSoup(rendered, 'html.parser')
    text = soup.get_text()
    assert all(text.count(topic+'。') == 1 for topic in ['中美关系','美债市场','中东局势'])
    assert len(soup.select('[data-macro-fact]')) == 9


def test_unknown_topics_share_one_neutral_section_without_invented_connections():
    items = [MacroNewsItem(t, NOW, f'https://example.com/{i}', 'Source') for i,t in enumerate([
        '甲国公布统计结果。', '乙国宣布会议日程。'])]
    html, notes = _rebuild_safe_html('<p>全球危机。编造关联[1]</p><p>另一主题。编造关联[2]</p>', items)
    soup = BeautifulSoup(html, 'html.parser')
    assert len(soup.select('p')) == 1 and '其他宏观。' in soup.get_text()
    assert '编造关联' not in soup.get_text() and len(notes) == 2
