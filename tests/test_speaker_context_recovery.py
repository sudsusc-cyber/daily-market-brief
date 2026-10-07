import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from src.collectors import news_context
from src.collectors.figures import FigureBundle, FigureMention
from src.processors.figure_filter import filter_one
from src.processors.news_selection import factual_excerpt
from src.processors.speaker_attribution import attribution, context_excerpt
from src.processors.thesis.extractor import _verified_grounding_row


def item(title='Acme CEO warns export controls could create a rival.'):
    return FigureMention(title, '', datetime(2026, 10, 3, tzinfo=UTC), 'https://www.cnbc.com/story', 'CNBC')


def test_surname_has_to_be_introduced_in_the_same_source_and_have_no_competing_name():
    news = item()
    claim = 'Smith warned export controls could accelerate competing technology.'
    news.source_body = 'Acme CEO Jane Smith said demand was strong. ' + claim
    binding = attribution(news, 'Jane Smith', excerpt=claim)
    assert binding['kind'] == 'resolved_surname'
    assert binding['full_name'] == 'Jane Smith'
    assert context_excerpt(news, 'Jane Smith') == claim
    news.source_body += ' John Smith said export controls pose risks.'
    assert not attribution(news, 'Jane Smith', excerpt=claim)
    assert not attribution(news, 'Jane Smith', excerpt='John Smith said export controls pose risks.')
    news.source_body = claim
    assert not attribution(news, 'Jane Smith', excerpt=claim)


def test_role_title_cannot_borrow_name_but_original_surname_statement_can_be_translated():
    title = 'ASML CEO warns excessive China export controls could create rival'
    full = 'CEO Christophe Fouquet told the Financial Times that the true bottleneck lies with ASML.'
    claim = 'Fouquet warned that overly broad export controls on advanced semiconductor technology to China could backfire by accelerating domestic alternatives.'
    news = item(title)
    news.source_body = full + '\n' + claim
    news.source_published_at = '2026-09-28T19:14:51+00:00'
    news.speaker_source_excerpt = context_excerpt(news, 'Christophe Fouquet')
    news.source_excerpt = factual_excerpt(news)
    news.translated_excerpt = 'Fouquet 警告，对向中国出口先进半导体技术实行过度广泛的管制，可能因加速本土替代技术的发展而适得其反。'
    assert news.source_excerpt == claim
    client = SimpleNamespace(chat=lambda *a, **k: SimpleNamespace(text='▦ 1: yes | score=5 | '+claim))
    result = filter_one(FigureBundle('Christophe Fouquet','query',items=[news]),client=client)
    assert result.items
    row = result.items[0].evidence[0]
    assert row['source_published_at'] == news.source_published_at
    assert _verified_grounding_row(result.items[0], row)
    assert not _verified_grounding_row(result.items[0], dict(row, source_body=claim))
    assert not _verified_grounding_row(result.items[0], dict(row, source_published_at='2026-08-31T00:00:00+00:00'))


def test_original_article_date_is_not_refreshed_by_rss_or_date_modified(monkeypatch):
    news = item()
    body = 'Acme CEO Jane Smith warned export controls could create a rival.'
    structured = {'@type':'NewsArticle','headline':news.title,'datePublished':'2026-08-31T10:00:00Z',
                  'dateModified':'2026-10-03T10:00:00Z'}
    html = ('<h1>'+news.title+'</h1><article><p>'+body+'</p></article>'
            '<script type="application/ld+json">'+json.dumps(structured)+'</script>').encode()
    monkeypatch.setattr(news_context, '_fetch', lambda url: html)
    news_context.enrich_speaker_context([FigureBundle('Jane Smith','query',items=[news])])
    assert news.source_published_at.startswith('2026-08-31')
    assert news.published_at == datetime(2026,10,3,tzinfo=UTC)
    assert not context_excerpt(news,'Jane Smith')
    assert not attribution(news,'Jane Smith',excerpt=body)
    structured['headline'] = 'Another article'
    assert news_context._published_at('<script type="application/ld+json">'+json.dumps(structured)+'</script>',news.title,news.source) == ''


def test_unsupported_publishers_do_not_consume_recoverable_source_budget(monkeypatch):
    calls=[]
    good=item()
    bad=[FigureMention(good.title,'',good.published_at,'https://news.google.com/rss/articles/example'+str(i),'unsupported.test') for i in range(4)]
    monkeypatch.setattr(news_context,'_fetch',lambda url: calls.append(url) or
                        ('<h1>'+good.title+'</h1><article><p>Acme CEO Jane Smith warned export controls could create a rival.</p></article>').encode())
    news_context.enrich_speaker_context([FigureBundle('Jane Smith','query',items=[*bad,good])])
    assert len(calls)==1 and good.speaker_context_diagnostic=='source_identity_bound'
    assert all(x.speaker_context_diagnostic=='source_date_unverified:unsupported_publisher' for x in bad)


@pytest.mark.parametrize('other', ['John A. Smith', 'JOHN SMITH', 'john smith', 'John van Smith', 'John Jr. Smith', 'Mary-Jane Smith', 'NotJane Smith'])
def test_initials_case_and_name_particles_cannot_hide_a_competing_person(other):
    news = item()
    news.source_body = f'Acme CEO Jane Smith said demand was strong. {other} warned export controls pose risks.'
    assert not attribution(news,'Jane Smith',excerpt=f'{other} warned export controls pose risks.')
    assert not attribution(news,'Jane Smith',excerpt='Smith warned export controls pose risks.')


def test_role_and_interview_appositions_preserve_clear_source_identity():
    news = item()
    news.source_body = "Jane Smith, Acme's chief executive, spoke at the conference. Smith warned export controls pose risks."
    assert context_excerpt(news,'Jane Smith') == 'Smith warned export controls pose risks.'
    news.source_body = 'Acme CEO Jane Smith was at the conference. Smith, speaking in an interview, warned export controls pose risks.'
    assert context_excerpt(news,'Jane Smith') == 'Smith, speaking in an interview, warned export controls pose risks.'


def test_current_article_cannot_refresh_dated_old_quote_or_future_source():
    news = item()
    news.source_published_at = '2026-09-28T00:00:00+00:00'
    news.source_body = 'Acme CEO Jane Smith said demand was strong. Smith said on August 31, 2026 that exports would fall.'
    assert not attribution(news,'Jane Smith',excerpt='Smith said on August 31, 2026 that exports would fall.')
    fresh = 'Jane Smith said on October 1, 2026 that exports would fall.'
    assert attribution(news,'Jane Smith',excerpt=fresh)
    news.source_published_at = '2026-12-01T00:00:00+00:00'
    assert not attribution(news,'Jane Smith',excerpt=fresh)
    news.source_published_at = ''
    assert not attribution(news,'黄仁勋',excerpt='黄仁勋在8月31日表示出口需求可能下降。')
    assert attribution(news,'黄仁勋',excerpt='黄仁勋在10月1日表示出口需求可能下降。')
    news.published_at = datetime(2026,10,3,16,10,tzinfo=UTC)
    news.source_published_at = '2026-10-04T00:05:00+08:00'
    assert attribution(news,'Jane Smith',excerpt='Jane Smith said demand was strong.')


def mirrored(title='Jane Smith warns export controls could create rivals.', suffix='current'):
    return FigureMention(title + ' - CNBC', '<a>' + title + '</a>  CNBC',
                         datetime(2026, 10, 3, tzinfo=UTC),
                         'https://news.google.com/rss/articles/' + suffix, 'CNBC')


def dated_html(news, published='2026-10-02T10:00:00Z', *, body=None):
    title = news.title.removesuffix(' - CNBC')
    metadata = {'@type': 'NewsArticle', 'headline': title, 'datePublished': published}
    html = '<h1>' + title + '</h1><script type="application/ld+json">' + json.dumps(metadata) + '</script>'
    if body is not None:
        html += '<article><p>' + body + '</p></article>'
    return html.encode()


def test_named_headline_mirror_also_fetches_original_date_and_rejects_old_article(monkeypatch):
    news = mirrored()
    calls = []
    monkeypatch.setattr(news_context, '_resolve', lambda url: 'https://www.cnbc.com/story')
    monkeypatch.setattr(news_context, '_fetch', lambda url: calls.append(url) or dated_html(news, '2026-08-31T10:00:00Z'))
    news_context.enrich_speaker_context([FigureBundle('Jane Smith', 'query', items=[news])])
    assert len(calls) == 1 and news.speaker_date_required
    assert news.source_published_at.startswith('2026-08-31')
    assert not attribution(news, 'Jane Smith', excerpt=news.title)
    assert news.published_at == datetime(2026, 10, 3, tzinfo=UTC)


def test_fresh_named_mirror_can_use_verified_date_without_supported_body_layout(monkeypatch):
    news = mirrored()
    monkeypatch.setattr(news_context, '_resolve', lambda url: 'https://www.cnbc.com/story')
    monkeypatch.setattr(news_context, '_fetch', lambda url: dated_html(news))
    news_context.enrich_speaker_context([FigureBundle('Jane Smith', 'query', items=[news])])
    assert news.source_published_at.startswith('2026-10-02')
    assert attribution(news, 'Jane Smith', excerpt=news.title)
    assert news.speaker_context_diagnostic == 'source_date_observed'
    assert not getattr(news, 'source_body', '')


def test_forbidden_publisher_stays_date_unverified_without_retry_or_bypass(monkeypatch):
    import requests
    news = mirrored()
    calls = []
    def denied(url):
        calls.append(url)
        raise requests.HTTPError('403')
    monkeypatch.setattr(news_context, '_resolve', lambda url: 'https://www.cnbc.com/story')
    monkeypatch.setattr(news_context, '_fetch', denied)
    news_context.enrich_speaker_context([FigureBundle('Jane Smith', 'query', items=[news])])
    assert len(calls) == 1 and not getattr(news, 'source_published_at', '')
    assert news.speaker_context_diagnostic == 'source_date_unverified:source_context_unavailable'
    assert not attribution(news, 'Jane Smith', excerpt=news.title)
    del news.speaker_date_required
    assert not attribution(news, 'Jane Smith', excerpt=news.title)


def test_original_publisher_and_full_feed_summary_keep_supplied_date(monkeypatch):
    direct = item('Jane Smith warns export controls could create rivals.')
    detailed = mirrored()
    detailed.snippet = 'Jane Smith warned that restrictions on licensing exports could encourage domestic competitors.'
    assert not news_context.requires_speaker_date(direct)
    assert not news_context.requires_speaker_date(detailed)
    monkeypatch.setattr(news_context, '_fetch', lambda url: (_ for _ in ()).throw(AssertionError('unnecessary fetch')))
    news_context.enrich_speaker_context([FigureBundle('Jane Smith', 'query', items=[direct, detailed])])
    assert attribution(direct, 'Jane Smith', excerpt=direct.title)
    assert attribution(detailed, 'Jane Smith', excerpt=detailed.title)


def test_role_identity_recovery_precedes_named_date_fetches_in_shared_budget(monkeypatch):
    named = [mirrored('Jane Smith warns demand may change ' + str(i) + '.', str(i)) for i in range(3)]
    role = item('Acme CEO warns export controls could create rivals.')
    role.url = 'https://www.cnbc.com/role'
    calls = []
    by_url = {'https://www.cnbc.com/' + str(i): news for i, news in enumerate(named)}
    monkeypatch.setattr(news_context, '_resolve', lambda url: 'https://www.cnbc.com/' + url.rsplit('/', 1)[-1])
    def fetch(url):
        calls.append(url)
        if url.endswith('/role'):
            return dated_html(role, body='Acme CEO Jane Smith warned export controls could create rivals.')
        return dated_html(by_url[url])
    monkeypatch.setattr(news_context, '_fetch', fetch)
    news_context.enrich_speaker_context([FigureBundle('Jane Smith', 'query', items=[*named, role])], max_requests=3)
    assert calls[0].endswith('/role') and len(calls) == 3
    assert role.speaker_context_diagnostic == 'source_identity_bound'
    assert named[-1].speaker_context_diagnostic == 'source_date_unverified:request_limit'


def test_existing_named_body_does_not_short_circuit_required_date_fetch(monkeypatch):
    news = mirrored('Acme CEO warns export controls could create rivals.')
    body = 'Acme CEO Jane Smith warned export controls could create rivals.'
    news.source_body = body
    calls = []
    monkeypatch.setattr(news_context, '_resolve', lambda url: 'https://www.cnbc.com/story')
    monkeypatch.setattr(news_context, '_fetch', lambda url: calls.append(url) or dated_html(news, body=body))
    news_context.enrich_speaker_context([FigureBundle('Jane Smith', 'query', items=[news])])
    assert len(calls) == 1 and news.source_published_at.startswith('2026-10-02')
    assert news.speaker_source_excerpt == body


def test_date_only_recovery_still_requires_article_heading_identity(monkeypatch):
    news = mirrored()
    html = dated_html(news).replace(b'<h1>Jane Smith warns export controls could create rivals.</h1>', b'<h1>Another article</h1>')
    monkeypatch.setattr(news_context, '_resolve', lambda url: 'https://www.cnbc.com/story')
    monkeypatch.setattr(news_context, '_fetch', lambda url: html)
    news_context.enrich_speaker_context([FigureBundle('Jane Smith', 'query', items=[news])])
    assert not getattr(news, 'source_published_at', '')
    assert news.speaker_context_diagnostic.startswith('source_date_unverified:')


def test_full_configured_name_does_not_outrank_role_recovery_because_date_is_missing(monkeypatch):
    # Huang is also a search surname. Missing the original date must not turn
    # an already fully named headline into an identity recovery request.
    named = mirrored('Jensen Huang says chip demand could improve.', 'huang')
    role = item('Acme CEO warns export controls could create rivals.')
    role.url = 'https://www.cnbc.com/role'
    calls = []
    monkeypatch.setattr(news_context, '_resolve', lambda url: 'https://www.cnbc.com/' + url.rsplit('/', 1)[-1])
    def fetch(url):
        calls.append(url)
        return (dated_html(role, body='Acme CEO Jane Smith warned export controls could create rivals.')
                if url.endswith('/role') else dated_html(named))
    monkeypatch.setattr(news_context, '_fetch', fetch)
    news_context.enrich_speaker_context([FigureBundle('黄仁勋', 'query', 'Jensen Huang', [named]),
                                        FigureBundle('Jane Smith', 'query', items=[role])])
    assert calls[0].endswith('/role')
    assert named.speaker_context_diagnostic == 'source_date_observed'
