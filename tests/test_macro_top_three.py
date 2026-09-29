"""Macro selection is capped by topic before evidence, citations and judgments."""
from datetime import UTC, datetime
from types import SimpleNamespace

from bs4 import BeautifulSoup

from src.collectors.macro_news import MacroFeedBundle, MacroNewsItem
from src.processors.macro_filter import (
    MacroNewsSummary,
    _rebuild_safe_html,
    limit_publication,
    merge_frontier_duplicates,
    summarize,
)
from src.processors.thesis.renderer import build_judgment_section, publication_sources
from src.renderer.render import render_email

NOW = datetime(2026, 9, 29, tzinfo=UTC)
FACTS = [
    '香港企业宣布上市。',
    'OpenAI 取消新模型发布。',
    '美国与中国举行贸易峰会。',
    '美联储宣布降息25基点。',
    '美国CPI通胀数据升至3%。',
    '美联储公布新的利率决定。',
]


def items():
    return [MacroNewsItem(t, NOW, f'https://example.com/{i}', 'Source') for i,t in enumerate(FACTS)]


def rebuild():
    evidence = []
    html, notes = _rebuild_safe_html('<p>无依据的拼接'+''.join(f'[{i}]' for i in range(1,7))+'</p>', items(), evidence)
    return MacroNewsSummary(html, notes, evidence)


def test_late_important_topics_replace_early_minor_topics_and_keep_related_updates():
    result = rebuild()
    soup = BeautifulSoup(result.summary_html, 'html.parser')
    assert len(soup.select('p')) == 3
    assert [p.select_one('span').get_text() for p in soup.select('p')] == ['货币政策。','通胀数据。','中美关系。']
    assert all(FACTS[i] in soup.get_text() for i in [2,3,4,5])
    assert all(FACTS[i] not in soup.get_text() for i in [0,1])
    assert [n.index for n in result.footnotes] == [1,2,3,4]
    assert {n.url for n in result.footnotes} == {r['url'] for r in result.evidence}
    assert len(soup.select('p')[0].select('a')) == 2


def test_retry_cannot_exceed_three_and_sparse_day_is_not_padded():
    replies=iter([SimpleNamespace(text='',error='timeout'),SimpleNamespace(text='<p>'+''.join(f'[{i}]' for i in range(1,7))+'</p>',error=None)])
    result=summarize([MacroFeedBundle('Source',items())],client=SimpleNamespace(chat=lambda *a,**kw:next(replies)))
    assert len(BeautifulSoup(result.summary_html,'html.parser').select('p')) == 3
    for count in [0,1,2]:
        html,notes=_rebuild_safe_html('<p>[1][2]</p>',items()[:count])
        assert len(BeautifulSoup(html,'html.parser').select('p')) == count
        assert len(notes) == count


def test_final_renderer_caps_legacy_summary_before_validating_its_judgment():
    evidence=[]
    notes=[]
    parts=[]
    for item in items():
        html, current=_rebuild_safe_html('<p>[1]</p>',[item],evidence)
        parts.append(html)
        notes.extend(current)
    legacy=MacroNewsSummary(''.join(parts), notes, evidence)
    judgment=build_judgment_section(today=NOW.date(), sources=publication_sources(macro_news=legacy))
    assert judgment and any('模型' in row['fact'] for row in judgment.items)
    final=limit_publication(legacy)
    assert len(BeautifulSoup(final.summary_html,'html.parser').select('p')) == 3
    rendered=render_email(signals=[],generated_at=NOW,macro_news_summary=legacy,judgment_section=judgment)
    assert FACTS[1].rstrip('。') not in BeautifulSoup(rendered,'html.parser').get_text()
    assert '产品发布调整后的长期影响' not in rendered
    assert len(legacy.evidence)==6  # final presentation never mutates originals


def test_frontier_merge_does_not_restore_a_dropped_topic_or_lose_visible_sources():
    result=rebuild()
    dropped_evidence=[]
    _rebuild_safe_html('<p>[1]</p>',[items()[1]],dropped_evidence)
    point=SimpleNamespace(text=FACTS[1],source_url=items()[1].url,evidence=dropped_evidence)
    unchanged,remaining,merged=merge_frontier_duplicates(result,[point])
    assert remaining==[point] and not merged
    assert len(BeautifulSoup(unchanged.summary_html,'html.parser').select('p'))==3
