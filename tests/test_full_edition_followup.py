from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from src.collectors.company_news import NewsItem
from src.processors.macro_events import edition_events
from src.processors.news_presentation import present, replay_presentation, voice_text
from src.processors.news_selection import factual_excerpt
from src.processors.source_grounding import checked_excerpt
from src.processors.translation_guard import translation_errors


def test_full_candidate_window_does_not_let_interviews_break_release_group():
    texts=['Labor market faltered in September as jobs increased by just 29,000, unemployment rate rose to 4.2%',
           'Traders now see little chance of a Fed rate hike in October after weak jobs report',
           'Nonfarm payrolls increased by 29,000 in September after downward revisions to the prior two months, according to Bureau of Labor Statistics data released Friday.',
           'Former CEA Chair on Jobs Report, GDP Growth',
           'Former Labor Secretary Rob Reich on Sept. Jobs Report']
    events=edition_events(texts)
    assert events[1].topic == '就业市场'
    assert events[1].decision == 'edition_data_release_reaction'


@pytest.mark.parametrize('source,translation,bad',[
    ('Acme can afford its dividend today.','Acme 目前有能力支付其股息。','Acme 今天已支付股息。'),
    ('Acme launches chips into orbit.','Acme 把芯片送入轨道。','Acme 取消发射芯片。'),
    ('Acme is an independent growth fund.','Acme 是独立成长基金。','Acme 正在增长。'),
])
def test_roles_not_isolated_words(source,translation,bad):
    assert not translation_errors(source,translation)
    assert translation_errors(source,bad)


def test_institution_context_uses_body_without_guessing_acronym():
    title='California subpoenas OpenAI over cybersecurity incidents — DOJ seeks developer liability'
    item=NewsItem(title,datetime.now(UTC),'https://example.com/legal','Source')
    assert factual_excerpt(item) == title.split(' — ')[0]
    item.source_body='California Attorney General issued an investigative subpoena to OpenAI over cybersecurity incidents.'
    assert factual_excerpt(item) == item.source_body
    item.source_excerpt=item.source_body
    item.translated_excerpt='加利福尼亚州 Attorney General 就网络安全事件向 OpenAI 发出调查传票。'
    # The raw source body is retained; no agency name inferred from DOJ.
    assert item.title == title


def test_unsupported_ambiguous_agency_does_not_guess_federal_identity():
    item=SimpleNamespace(title='DOJ investigates OpenAI liability',summary='',source='Source')
    assert factual_excerpt(item) == ''


def test_contextual_lead_remains_source_bound_for_translation():
    item=NewsItem('California subpoenas OpenAI over hacking — DOJ seeks developer liability',datetime.now(UTC),'https://example.com/legal','Source')
    item.source_excerpt=factual_excerpt(item)
    item.translated_excerpt='加利福尼亚州就黑客攻击向 OpenAI 发出传票'
    assert checked_excerpt(item)[1] == item.translated_excerpt


@pytest.mark.parametrize('person,issuer', [('黄仁勋','Nvidia'),('纳德拉','Microsoft')])
def test_voice_leads_with_speech_preserves_deal_background_and_replays(person,issuer):
    text=f'{issuer} 股价在 35 亿美元投资后上涨——{person}称公司“不惧”竞争。'
    shown=voice_text(text,person)
    assert shown.startswith(issuer+'“不惧”竞争')
    assert '35 亿美元' in shown and person not in shown
    row={'presentation_version':14,'presentation_speaker':person}
    assert replay_presentation(text,row) == voice_text(present(text).text,person)
    assert voice_text(text,person,_version=13) == text


def test_another_speaker_and_non_neutral_attribution_remain_explicit():
    text='Nvidia 股价上涨——黄仁勋否认收购计划。'
    assert voice_text(text,'黄仁勋') == text
    assert voice_text('Nvidia 股价上涨——纳德拉称需求强劲。','黄仁勋').endswith('纳德拉称需求强劲。')


def test_written_payment_and_consecutive_duration_keep_amount_units():
    source='Acme cut another quarterly check to shareholders after 64 consecutive years of dividend raises.'
    good='Acme 在连续 64 年提高股息后又向股东开出一张季度支票。'
    assert not translation_errors(source,good)
    assert translation_errors(source,good.replace('64','65'))
    assert translation_errors(source,'Acme 削减季度股息。')


def test_agentic_expansion_does_not_invent_new_technology():
    assert not translation_errors('Acme announced agentic infrastructure.', 'Acme 宣布 AI 智能体基础设施。')
    assert translation_errors('Acme announced infrastructure.', 'Acme 宣布 AI 智能体基础设施。')


def test_actual_full_edition_keeps_release_with_reaction_under_three_paragraph_cap():
    import json
    from pathlib import Path

    from src.processors.macro_filter import _rebuild_safe_html

    fixture=json.loads((Path(__file__).parent/'fixtures/october3_1954_macro_window.json').read_text())
    items=[]
    for row in fixture['rows']:
        item=NewsItem(row['title'],datetime.fromisoformat(row['published_at']),row['url'],row['source'],row['snippet'])
        item.source_excerpt=row['source_excerpt']
        item.translated_excerpt=row['translated_excerpt']
        items.append(item)
    raw=''.join('<p>'+''.join(f'[{i}]' for i in indexes)+'</p>' for indexes in fixture['paragraphs'])
    evidence=[]
    html,notes=_rebuild_safe_html(raw,items,evidence,editorial_order=True)
    assert html.count('<p ') == 3
    assert '29,000' in html and '4.2%' in html
    assert '加息' in html
    assert '中东局势' in html and '债券市场' in html
    assert len(notes)>=5
    assert '前 CEA 主席谈' not in html and '前劳工部长 Rob Reich 谈' not in html
    topics={r['macro_topic'] for r in evidence if 'jobs-report-september' in r['url'] or 'rate-hike-odds' in r['url']}
    assert topics == {'就业市场'}


def test_context_enrichment_attempts_body_even_with_safe_lead(monkeypatch):
    from src.collectors import news_context

    title='California subpoenas OpenAI over hacking — DOJ seeks developer liability'
    item=NewsItem(title,datetime.now(UTC),'https://www.tomshardware.com/test','Source')
    called=[]
    def fetch(url):
        called.append(url)
        return f'<h1>{title}</h1><article><p>California Attorney General issued a subpoena to OpenAI.</p></article>'.encode()
    monkeypatch.setattr(news_context,'_fetch',fetch)
    news_context.enrich_technical_context([item])
    assert called == [item.url]
    assert item.context_diagnostic == 'verified_context'
    assert factual_excerpt(item) == 'California Attorney General issued a subpoena to OpenAI.'


def test_correction_or_denial_in_tail_cannot_be_removed():
    item=SimpleNamespace(title='California subpoenas OpenAI over hacking — DOJ denies the report',summary='',source='Source')
    assert factual_excerpt(item) == ''


def test_interview_date_is_not_a_released_measurement():
    from src.processors.macro_events import _is_data_release

    assert not _is_data_release('Former official reported on jobs on September 29, 2026')
    assert _is_data_release('US payrolls increased by 29,000 in September')
