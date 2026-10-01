"""Actual sent inputs from run 36519910922, no credentials or recipient data."""
import copy
import json
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from bs4 import BeautifulSoup

from src.collectors.company_news import NewsItem
from src.collectors.macro_news import MacroNewsItem
from src.processors.macro_filter import _rebuild_safe_html
from src.processors.macro_topics import macro_topic
from src.processors.news_presentation import publication_text
from src.processors.news_selection import company_candidate, factual_excerpt, frontier_candidate
from src.processors.news_summarizer import _rebuild_safe_summary
from src.processors.source_grounding import grounded_text
from src.processors.thesis.renderer import _rule_for, build_judgment_section
from src.sender.smtp_sender import _html_to_plain

ROWS=json.loads((Path(__file__).parent/'fixtures/september29_noon_semantics.json').read_text())


def article(row, **kwargs):
    return SimpleNamespace(title=row['original_title'],summary=row['original_summary'],source=row['source_name'],
        source_excerpt=row['excerpt'],translated_excerpt=row['validated_text'],url=row['url'],
        published_at=row['published_at'],**kwargs)


def test_actual_roundup_does_not_publish_hsbc_amount_as_tencent_news():
    row=next(r for r in ROWS['company'] if '汇丰' in r['output_text'])
    it=article(row)
    before=copy.deepcopy(vars(it))
    assert not company_candidate(it,'0700.HK')
    news=NewsItem(it.title,datetime.fromisoformat(it.published_at),it.url,it.source,
                  summary=it.summary,holding_ticker='0700.HK')
    assert _rebuild_safe_summary('<strong>腾讯</strong>——'+row['output_text']+'[1]',[news]) is None
    assert vars(it)==before


def test_complete_tencent_sentence_in_roundup_remains_usable_but_other_company_does_not():
    it=SimpleNamespace(title='回购集合 | 汇丰控股、腾讯控股回购',summary='汇丰控股耗资1.37亿港元回购。腾讯控股回购100万股。',
                       holding_ticker='0700.HK',url='https://example.com/roundup',source='Source')
    assert company_candidate(it,'0700.HK')
    assert factual_excerpt(it)=='腾讯控股回购100万股。'
    text,rows=grounded_text('汇丰控股耗资1.37亿港元回购。',[it])
    # Final company gate must reject another sentence even if it is in the source.
    from src.processors.news_selection import company_fact_matches
    # Publication now rejects the unrelated excerpt before binding and safely
    # falls back to the holding's own complete source sentence.
    assert company_fact_matches(text,'0700.HK')
    assert '汇丰' not in text
    text,rows=grounded_text('腾讯控股回购100万股。',[it])
    assert text=='腾讯控股回购100万股。' and rows[0]['original_summary']==it.summary
    news=NewsItem(it.title,datetime(2026,9,29),it.url,it.source,summary=it.summary,holding_ticker='0700.HK')
    rejected=_rebuild_safe_summary('<strong>腾讯</strong>——汇丰控股耗资1.37亿港元回购。[1]',[news])
    assert rejected is not None
    assert '回购100万股' in rejected.summary_html
    assert '腾讯' in rejected.summary_html
    assert '汇丰' not in rejected.summary_html
    accepted=_rebuild_safe_summary('<strong>腾讯</strong>——腾讯控股回购100万股。[1]',[news])
    assert accepted and '100万股' in accepted.summary_html and '1.37' not in accepted.summary_html


def test_frontier_promo_is_replaced_only_by_an_independent_complete_fact():
    row=ROWS['frontier'][0]
    it=article(row)
    before=copy.deepcopy(vars(it))
    assert frontier_candidate(it)
    assert factual_excerpt(it)=='Anthropic Will Soon Go Public.'
    assert grounded_text(row['validated_text'],[it])==('',[])
    it.source_excerpt=factual_excerpt(it)
    it.translated_excerpt='Anthropic 即将上市。'
    text,evidence=grounded_text(it.translated_excerpt,[it])
    assert text=='Anthropic 即将上市。'
    assert evidence[0]['original_title']==before['title']
    assert '让开' not in text and '至关重要' not in text
    noise=SimpleNamespace(title='Move Over, SpaceX. Why It Could Be Critical for AI Stocks',summary='')
    assert not frontier_candidate(noise)


def test_nested_publisher_tails_are_not_news_but_attribution_and_other_dash_text_remain():
    row=ROWS['frontier'][1]
    it=article(row)
    original=copy.deepcopy(vars(it))
    assert grounded_text(row['validated_text'],[it])==('',[])  # headline lacks the scope of cancellation
    text=publication_text(row['validated_text'],source_name=it.source)
    assert 'ABC News Australia' not in text and 'UA.NEWS' not in text
    assert 'GPT-6.1 Astra' in text and '取消' in text
    assert vars(it)==original
    for text in ['ABC News Australia 报道，公司尚未获批。','公司称计划未变 — 仍待批准','公司宣布计划 — Unknown Outlet']:
        assert publication_text(text,source_name='UA.NEWS')==text


def test_country_alone_cannot_merge_growth_policy_with_talent_restrictions():
    facts=['中国将推出新的促增长政策','中国将出行限制扩大至顶尖 AI 人才及其家属','日本40年期国债发行需求创2020年以来最强']
    assert [macro_topic(t) for t in facts]==['经济增长','科技监管','债券市场']
    assert macro_topic('中国宣布一项未说明事项')=='其他宏观'
    items=[MacroNewsItem(t,datetime(2026,9,29),f'https://example.com/{i}','Source') for i,t in enumerate(facts)]
    evidence=[]
    html,notes=_rebuild_safe_html('<p>同一国家[1][2][3]</p>',items,evidence)
    assert len(BeautifulSoup(html,'html.parser').select('p'))==3
    assert len(notes)==len(evidence)==3


@pytest.mark.parametrize('original,translated,key',[
    ('OpenAI cancels GPT-6.1 Astra','OpenAI 取消 GPT-6.1 Astra','product-release-risk'),
    ('OpenAI releases GPT-6.1 Astra','OpenAI 发布 GPT-6.1 Astra','product-commercialization'),
    ('Google data center is under construction','谷歌数据中心在建','infrastructure-investment'),
    ('Google data center is not under construction','谷歌数据中心并非在建',None),
])
def test_named_products_and_construction_keep_event_state(original,translated,key):
    rule=_rule_for({'excerpt':original,'output_text':translated})
    assert (rule.key if rule else None)==key


def test_actual_news_can_supply_bounded_judgments_without_inventing_facts():
    for row in [next(r for r in ROWS['company'] if 'Crusoe' in r['output_text'])]:
        it=article(row)
        text,evidence=grounded_text(row['validated_text'],[it])
        obj=SimpleNamespace(summary_html=text,evidence=evidence,footnotes=[SimpleNamespace(url=it.url)])
        result=build_judgment_section(sources={'company_news':[obj]},today=date(2026,9,29))
        assert result and result.items[0]['fact']==text
        assert result.items[0]['evidence']['excerpt']==row['excerpt']


def test_plain_sources_get_separate_lines_and_safe_urls_without_html_dots():
    html='<p>正文<sup><a href="https://example.com/a">[1]</a></sup></p><div>'+''.join(
        f'<a class="source-link" href="https://example.com/{i}?a=1&amp;b=2">[{i}] Source</a>' for i in [1,2])+'</div>'
    plain=_html_to_plain(html)
    assert '[1] Source https://example.com/1?a=1&b=2\n[2] Source https://example.com/2?a=1&b=2' in plain
    assert '·' not in plain
    assert 'javascript:' not in _html_to_plain('<a class="source-link" href="javascript:evil()">[1] Source</a>')


def test_known_names_are_localized_after_validation_without_changing_dates_or_numbers():
    assert publication_text('Saudi Arabia 恢复出口，Iran 对 Strait of Hormuz 的影响。')=='沙特阿拉伯恢复出口，伊朗对霍尔木兹海峡的影响。'
    assert publication_text('Satya Nadella 表示2026年增长10%。')=='萨提亚·纳德拉表示2026年增长10%。'
    assert publication_text('Iranian 公司营收10亿美元。')=='Iranian 公司营收10亿美元。'
