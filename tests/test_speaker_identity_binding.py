from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from src.collectors import news_context
from src.collectors.figures import FIGURES, FigureBundle, FigureMention
from src.processors.figure_filter import filter_one
from src.processors.news_selection import factual_excerpt
from src.processors.speaker_attribution import attribution, context_excerpt, needs_speaker_context
from src.processors.thesis.extractor import _verified_grounding_row


def item(title, snippet=''):
    return FigureMention(title, snippet, datetime(2026, 10, 3, tzinfo=UTC),
                         'https://www.cnbc.com/report', 'CNBC')


def test_all_configured_people_bind_by_original_names_not_search_query():
    for cn, _, _, en in FIGURES:
        assert attribution(item(f'{en} says demand for chips will increase.'), cn, en)
        assert attribution(item(f'{cn}表示企业需求仍受预算约束。'), cn, en)
    assert not attribution(item('巴菲特表示科技企业需求仍受预算约束。'), '黄仁勋', 'Jensen Huang')


@pytest.mark.parametrize('title', [
    'Jane Smith says Jensen Huang is wrong about chip demand.',
    'ASML CEO warns export controls could create a rival.',
    'ASML says export controls could create a rival.',
    'Investors discuss Christophe Fouquet and call for export controls.',
])
def test_mention_role_and_company_statement_cannot_assign_a_person(title):
    target = '黄仁勋' if 'Huang' in title else 'Christophe Fouquet'
    assert not attribution(item(title), target)


def test_unnamed_role_recovers_named_statement_itself_without_borrowing_identity():
    news = item('Acme CEO warns export controls could create a rival.')
    assert needs_speaker_context(news, 'Jane Smith')
    news.source_body = 'Acme CEO Jane Smith said export controls could accelerate competing technology.'
    assert not attribution(news, 'Jane Smith', excerpt=news.title)
    assert context_excerpt(news, 'Jane Smith') == news.source_body
    assert attribution(news, 'Jane Smith', excerpt=news.source_body)
    news.source_body = ('Acme CEO Jane Smith said demand was strong. '
                        'Former CEO John Smith warned in 2022 that export controls would create a rival.')
    assert not attribution(news, 'Jane Smith', excerpt=news.title)
    assert context_excerpt(news, 'Jane Smith') == 'Acme CEO Jane Smith said demand was strong.'


@pytest.mark.parametrize('name,target', [('Peter Buffett', '巴菲特'), ('Andrew Huang', '黄仁勋'),
                                        ('Jane Abel', '阿贝尔'), ('彼得·巴菲特', '巴菲特')])
def test_search_surnames_do_not_bind_a_different_full_name(name, target):
    assert not attribution(item(f'{name} said philanthropy must change.'), target)


def test_role_spelling_and_bounded_biographical_apposition_do_not_lose_named_speech():
    for text in ('ASML chief executive officer Christophe Fouquet said export controls pose risks.',
                 'ASML CEO Christophe Fouquet, who took office in 2024, warned export controls pose risks.'):
        assert attribution(item(text), 'Christophe Fouquet')


@pytest.mark.parametrize('text', ['英伟达否认黄仁勋表示芯片需求将下降。',
                                  'Unconfirmed reports claim Jensen Huang says demand will fall.',
                                  'If Jensen Huang says chip demand will fall, investors may worry.',
                                  'Jensen Huang: The cost of AI'])
def test_denial_hypothesis_and_topic_heading_are_not_statements(text):
    assert not attribution(item(text), '黄仁勋')


def test_historical_quote_cannot_be_recovered_as_current_statement():
    news = item('ASML CEO warns export controls could create a rival.')
    news.source_body = 'Christophe Fouquet said in 2020 that export controls could create a rival.'
    assert not context_excerpt(news, 'Christophe Fouquet')
    assert not attribution(news, 'Christophe Fouquet', excerpt=news.source_body)
    news.source_body = 'Christophe Fouquet said demand is above 2020 levels.'
    assert context_excerpt(news, 'Christophe Fouquet') == news.source_body
    assert attribution(item('黄仁勋表示芯片需求不会下降。'), '黄仁勋')


def test_different_statement_in_same_article_cannot_approve_excerpt_speaker():
    news = item('巴菲特表示云计算客户需要审慎规划预算。', '黄仁勋表示数据中心需求增长。')
    assert attribution(news, '黄仁勋')
    assert not attribution(news, '黄仁勋', excerpt=news.title)


def test_real_byline_requires_source_binding_even_if_model_accepts_wrong_person():
    news = item('巴菲特表示云计算客户需要审慎规划预算。')
    client = SimpleNamespace(chat=lambda *a, **k: SimpleNamespace(text='▦ 1: yes | score=5 | '+news.title))
    result = filter_one(FigureBundle('黄仁勋', 'query', 'Jensen Huang', [news]), client=client)
    assert not result.items
    assert result.content_rejections == ['index=1 reason=speaker_identity_unverified']


def test_binding_is_archived_and_revalidated_by_thesis():
    news = item('黄仁勋表示云计算客户需要审慎规划预算。')
    client = SimpleNamespace(chat=lambda *a, **k: SimpleNamespace(text='▦ 1: yes | score=5 | '+news.title))
    result = filter_one(FigureBundle('黄仁勋', 'query', 'Jensen Huang', [news]), client=client)
    point = result.items[0]
    row = point.evidence[0]
    assert row['speaker_attribution']['person'] == '黄仁勋'
    assert _verified_grounding_row(point, row)
    assert not _verified_grounding_row(point, dict(row, presentation_speaker='巴菲特'))
    forged = dict(row, speaker_attribution=dict(row['speaker_attribution'], excerpt='unrelated'))
    assert not _verified_grounding_row(point, forged)
    missing_binding = {key: value for key, value in row.items() if key != 'speaker_attribution'}
    assert not _verified_grounding_row(point, missing_binding)
    unknown_date = dict(row, url='https://news.google.com/rss/articles/example', speaker_date_required=True)
    assert not _verified_grounding_row(point, unknown_date)
    unknown_date.pop('speaker_attribution')
    assert not _verified_grounding_row(point, unknown_date)
    assert not _verified_grounding_row(point, dict(unknown_date, presentation_version=2))


def test_characterization_is_speech_but_a_phone_call_is_not():
    assert attribution(item('Jensen Huang called model distillation "competition".'), '黄仁勋')
    assert attribution(item('Jane Smith described the chip market as competitive.'), 'Jane Smith')
    assert not attribution(item('Jensen Huang called a taxi after the conference.'), '黄仁勋')


def test_context_recovery_is_bounded_and_keeps_rss_immutable(monkeypatch):
    title = 'Acme CEO warns export controls could create a rival.'
    body = 'Acme CEO Jane Smith said export controls could accelerate competing technology.'
    news = item(title)
    calls = []
    monkeypatch.setattr(news_context, '_fetch', lambda url: calls.append(url) or
                        f'<h1>{title}</h1><article><p>{body}</p></article>'.encode())
    news_context.enrich_speaker_context([FigureBundle('Jane Smith', 'query', items=[news])])
    assert news.title == title and news.snippet == ''
    assert attribution(news, 'Jane Smith') and news.context_url == news.url
    assert factual_excerpt(news) == body
    assert news.speaker_context_diagnostic == 'source_identity_bound'
    assert len(calls) == 1
    monkeypatch.setenv('BRIEF_LLM_CUTOFF_EPOCH', '0')
    other = item(title)
    news_context.enrich_speaker_context([FigureBundle('Jane Smith', 'query', items=[other])])
    assert other.speaker_context_diagnostic == 'context_timeout' and len(calls) == 1
