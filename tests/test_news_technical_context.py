import json
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.collectors import news_context
from src.collectors.figures import FigureMention
from src.processors.news_presentation import voice_text
from src.processors.news_selection import factual_excerpt
from src.processors.source_grounding import grounded_text
from src.processors.technical_context import contextual_excerpt, needs_technical_context
from src.processors.thesis.extractor import _verified_grounding_row
from src.processors.translation_guard import translation_errors
from src.processors.translator import translate_in_place_news

FIXTURE = json.loads((Path(__file__).parent/'fixtures/ai_distillation_context.json').read_text())
TRANSLATION = ('Nvidia CEO Jensen Huang (젠슨 황) 将基于另一个 AI 模型的输出来训练新人工智能（AI）模型的“蒸馏”技术称为“竞争”。'
               '这与美国政府将通过蒸馏提取美国 AI 模型能力视为“窃取”的观点不同。')


def item():
    return FigureMention(FIXTURE['title'] + ' - ' + FIXTURE['source'], FIXTURE['title'],
                         datetime(2026, 9, 29, tzinfo=UTC), FIXTURE['url'], FIXTURE['source'])


def article(title=None, paragraph=None):
    return ('<h1>'+escape(title or FIXTURE['title'])+'</h1><article id="article-view-content-div"><p>'
            +escape(paragraph or FIXTURE['paragraph'])+'</p></article>').encode()


def test_actual_rss_cannot_substitute_vague_title_for_technical_context():
    it = item()
    assert needs_technical_context(it.title)
    assert factual_excerpt(it) == ''
    it.source_excerpt = it.title
    it.translated_excerpt = 'Jensen Huang 称使用他人的 AI 模型是竞争，与美国视其为窃取的观点相冲突'
    assert grounded_text(it.translated_excerpt, [it]) == ('', [])
    it.translated_excerpt = 'Jensen Huang 称蒸馏他人的 AI 模型是竞争。'
    assert grounded_text(it.translated_excerpt, [it]) == ('', [])


def test_enriched_body_is_translated_and_bound_without_overwriting_rss(monkeypatch):
    it = item()
    original = (it.title, it.snippet)
    monkeypatch.setattr(news_context, '_fetch', lambda url: article())
    news_context.enrich_technical_context([it])
    assert it.context_diagnostic == 'verified_context'
    assert (it.title, it.snippet) == original
    assert it.context_url == FIXTURE['url'] and it.context_fetched_at
    calls = []
    def chat(prompt, **kwargs):
        calls.append(prompt)
        return SimpleNamespace(text='▦ 1: '+TRANSLATION)
    translate_in_place_news([it], client=SimpleNamespace(chat=chat))
    assert 'distillation' in calls[0] and 'output' in calls[0]
    assert not translation_errors(it.source_excerpt, TRANSLATION)
    published, rows = grounded_text(TRANSLATION, [it])
    assert '蒸馏' in published and rows
    row = rows[0]
    assert row['source_body'] == FIXTURE['paragraph']
    assert row['context_url'] == it.context_url
    assert row['original_title'] == original[0] and row['original_summary'] == original[1]
    assert _verified_grounding_row(SimpleNamespace(text=published), row)
    assert not _verified_grounding_row(SimpleNamespace(text=published), dict(row, source_body=''))
    display = voice_text(published, '黄仁勋')
    assert display.startswith('将') and 'Jensen Huang' not in display and '蒸馏' in display
    voice_row = dict(row, output_text=display, presentation_version=2, presentation_speaker='黄仁勋')
    assert _verified_grounding_row(SimpleNamespace(text=display), voice_row)


@pytest.mark.parametrize(('original','translation'), [
    ('AI model distillation is competition.', 'AI 模型使用属于竞争。'),
    ('Using AI models is competition.', 'AI 模型蒸馏属于竞争。'),
    ('Fine-tuning an AI model improves performance.', '使用 AI 模型可以提高性能。'),
])
def test_technical_method_cannot_be_lost_or_invented(original, translation):
    assert any(e.startswith('technical_method:') for e in translation_errors(original, translation))


def test_failed_fetch_and_budget_exhaustion_do_not_publish_headline(monkeypatch):
    it = item()
    monkeypatch.setattr(news_context, '_fetch', lambda url: (_ for _ in ()).throw(ValueError('missing')))
    news_context.enrich_technical_context([it])
    assert it.context_diagnostic == 'missing' and not factual_excerpt(it)
    monkeypatch.setenv('BRIEF_LLM_CUTOFF_EPOCH', '0')
    monkeypatch.setattr(news_context, '_fetch', lambda url: pytest.fail('expired budget fetched'))
    news_context.enrich_technical_context([it])
    assert it.context_diagnostic == 'context_timeout' and not factual_excerpt(it)


def test_page_identity_and_supported_hosts_are_required():
    with pytest.raises(ValueError, match='title_mismatch'):
        news_context._body(article(title='Unrelated report'), FIXTURE['title'], FIXTURE['source'])
    for url in ('http://www.digitaltoday.co.kr/a', 'https://127.0.0.1/a',
                'https://www.digitaltoday.co.kr.evil.test/a', 'https://user:pass@www.digitaltoday.co.kr/a'):
        with pytest.raises(ValueError, match='unsupported_publisher'):
            news_context._publisher_url(url)


def test_body_without_technical_method_cannot_unlock_generic_headline(monkeypatch):
    it = item()
    monkeypatch.setattr(news_context, '_fetch', lambda url: article(paragraph='Jensen Huang says using other AI models is competition.'))
    news_context.enrich_technical_context([it])
    assert it.context_diagnostic == 'insufficient_context' and not contextual_excerpt(it)
    assert grounded_text('Jensen Huang 称使用其他 AI 模型属于竞争。', [it]) == ('', [])


def test_existing_real_summary_can_supply_context_without_fetch(monkeypatch):
    it = item()
    it.snippet = FIXTURE['paragraph']
    monkeypatch.setattr(news_context, '_fetch', lambda url: pytest.fail('unnecessary fetch'))
    news_context.enrich_technical_context([it])
    assert 'distillation' in factual_excerpt(it)


def test_request_count_is_bounded_and_repeated_sources_reused(monkeypatch):
    calls = []
    monkeypatch.setattr(news_context, '_fetch', lambda url: calls.append(url) or article())
    entries = [item() for _ in range(5)]
    entries[2].url += '?two'
    entries[3].url += '?three'
    entries[4].url += '?four'
    news_context.enrich_technical_context(entries)
    assert len(calls) == 3
    assert entries[1].source_body == entries[0].source_body
    assert entries[-1].context_diagnostic == 'context_request_limit'


@pytest.mark.parametrize(('status', 'content_type', 'payload', 'reason'), [
    (302, 'text/html', b'', 'redirect_or_non_article'),
    (200, 'application/json', b'{}', 'not_html'),
    (200, 'text/html', b'x' * 1_000_001, 'article_too_large'),
])
def test_article_fetch_bounds_redirects_types_and_size(monkeypatch, status, content_type, payload, reason):
    class Response:
        status_code = status
        headers = {'Content-Type': content_type}
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def raise_for_status(self):
            pass
        def iter_content(self, _size):
            yield payload
    class Session:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def get(self, url, **kwargs):
            assert kwargs['allow_redirects'] is False
            assert kwargs['stream'] is True and kwargs['timeout'] == (4, 6)
            assert self.trust_env is False
            return Response()
    monkeypatch.setattr(news_context.requests, 'Session', Session)
    with pytest.raises(ValueError, match=reason):
        news_context._fetch(FIXTURE['url'])


def test_another_speakers_distillation_statement_cannot_replace_huang():
    it = item()
    it.source_body = 'Scott Bessent said AI model distillation is theft, disagreeing with Jensen Huang.'
    assert contextual_excerpt(it) == ''
    it.source_body += '\n' + FIXTURE['paragraph']
    assert contextual_excerpt(it).startswith('Nvidia CEO Jensen Huang')
