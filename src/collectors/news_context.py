"""Bounded article enrichment for headline-only AI technique disputes.

Only explicitly supported publisher hosts are fetched. RSS fields are immutable;
article identity and body provenance are retained separately for audit.
"""
import json
import logging
import re
from datetime import UTC, datetime
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup

from src.processors.technical_context import (
    contextual_excerpt,
    needs_institution_context,
    needs_technical_context,
)
from src.utils.news_facts import canonical_fact
from src.utils.runtime_budget import RuntimeBudget

logger = logging.getLogger(__name__)
_HOSTS = {'www.digitaltoday.co.kr', 'digitaltoday.co.kr', 'www.cnbc.com', 'www.tomshardware.com', 'tomshardware.com',
          'www.reuters.com', 'www.ft.com', 'www.bloomberg.com', 'www.benzinga.com', 'www.asml.com',
          'www.microsoft.com', 'blogs.nvidia.com', 'www.nvidia.com', 'www.amd.com', 'scanx.trade'}
_SOURCE_LABELS = {'reuters', 'financial times', 'ft', 'bloomberg', 'benzinga', 'asml',
                  'microsoft blog', 'nvidia blog', 'nvidia', 'amd', 'amd ir', 'cnbc', 'scanx.trade',
                  'digitaltoday', 'digital today', "tom's hardware"}
_MAX_BYTES = 1_000_000


def _publisher_url(url):
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or parsed.hostname not in _HOSTS
            or parsed.username or parsed.password or parsed.port not in (None, 443)):
        raise ValueError('unsupported_publisher')
    return url


def _resolve(url):
    parsed = urlsplit(url)
    if parsed.scheme == 'https' and parsed.hostname == 'news.google.com' and parsed.path.startswith('/rss/articles/'):
        from googlenewsdecoder import new_decoderv1
        result = new_decoderv1(url)
        if not result.get('status'):
            raise ValueError('rss_resolution_failed')
        url = result.get('decoded_url', '')
    return _publisher_url(url)


def _fetch(url):
    # No automatic redirects to an unvalidated host; no credentials/cookies.
    with requests.Session() as session:
        session.trust_env = False
        with session.get(_publisher_url(url), timeout=(4, 6), stream=True, allow_redirects=False) as response:
            response.raise_for_status()
            if response.status_code != 200:
                raise ValueError('redirect_or_non_article')
            if 'text/html' not in response.headers.get('Content-Type', '').lower():
                raise ValueError('not_html')
            data = bytearray()
            for chunk in response.iter_content(16384):
                data.extend(chunk)
                if len(data) > _MAX_BYTES:
                    raise ValueError('article_too_large')
            return bytes(data)


def _body(html, title, source):
    soup = BeautifulSoup(html, 'html.parser')
    headline = re.sub(r'\s*[-–—|]\s*' + re.escape(source) + r'\s*$', '', title) if source else title
    headings = [canonical_fact(h.get_text(' ', strip=True)) for h in soup.select('h1')]
    if canonical_fact(headline) not in headings:
        raise ValueError('article_title_mismatch')
    article = soup.select_one('#article-view-content-div, [itemprop="articleBody"], .ArticleBody-articleBody, article, #article-body')
    if article is None:
        raise ValueError('article_body_missing')
    for tag in article.select('script, style, nav, aside, figure, footer'):
        tag.decompose()
    paragraphs = []
    size = 0
    for p in article.select('p'):
        text = p.get_text(' ', strip=True)
        if size + len(text) > 12000:
            break
        if text:
            paragraphs.append(text)
            size += len(text)
    if not paragraphs:
        raise ValueError('article_body_empty')
    return '\n'.join(paragraphs)


def _published_at(html, title, source):
    """Only datePublished for this exact article, never dateModified/fetch time."""
    soup = BeautifulSoup(html, 'html.parser')
    headline = re.sub(r'\s*[-–—|]\s*' + re.escape(source) + r'\s*$', '', title) if source else title
    values = []
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            pending = [json.loads(script.string or script.get_text())]
        except (ValueError, TypeError):
            continue
        while pending:
            node = pending.pop()
            if isinstance(node, list):
                pending.extend(node)
            elif isinstance(node, dict):
                if (canonical_fact(str(node.get('headline', ''))) == canonical_fact(headline)
                        and node.get('datePublished')):
                    try:
                        value = datetime.fromisoformat(str(node['datePublished']).replace('Z', '+00:00'))
                        if value.tzinfo is not None:
                            values.append(value)
                    except (ValueError, TypeError):
                        pass
                pending.extend(value for value in node.values() if isinstance(value, (dict, list)))
    return min(values).isoformat() if values else ''


def enrich_technical_context(items):
    """At most 3 sources / 20 seconds total, within the workflow cutoff."""
    budget = RuntimeBudget(seconds=20)
    cache = {}
    attempts = 0
    for item in items:
        institutional = needs_institution_context(item.title)
        if not (needs_technical_context(item.title) or institutional):
            continue
        if contextual_excerpt(item) and (not institutional or getattr(item, 'source_body', '')):
            continue
        url = item.url
        key = (url, item.title, item.source)
        if key not in cache:
            if attempts >= 3:
                item.context_diagnostic = 'context_request_limit'
                continue
            attempts += 1

            def fetch_one(item=item, url=url):
                try:
                    resolved = _resolve(url)
                    body = _body(_fetch(resolved), item.title, item.source)
                    return {'source_body': body, 'context_url': resolved,
                            'context_fetched_at': datetime.now(UTC).isoformat(), 'context_diagnostic': 'fetched'}
                except (ValueError, requests.RequestException) as exc:
                    logger.warning('news.context_unavailable reason=%s', type(exc).__name__)
                    return {'context_diagnostic': str(exc)[:80] if isinstance(exc, ValueError) else 'fetch_failed'}

            cache[key] = budget.call(fetch_one, seconds=12, fallback=lambda: {'context_diagnostic': 'context_timeout'})
        for field, value in cache[key].items():
            setattr(item, field, value)
        if getattr(item, 'source_body', ''):
            from src.processors.news_selection import plain_source

            excerpt = contextual_excerpt(item)
            item.context_diagnostic = ('verified_context' if excerpt and excerpt in plain_source(item.source_body)
                                       else 'headline_lead_only' if institutional and excerpt else 'insufficient_context')
        logger.info('news.context_result status=%s', item.context_diagnostic)


def enrich_speaker_context(bundles):
    """Recover role-only speech from the article, with a section-wide budget."""
    from src.processors.speaker_attribution import context_excerpt, needs_speaker_context

    budget = RuntimeBudget(seconds=20)
    cache = {}
    for bundle in bundles:
        for item in bundle.items:
            if not needs_speaker_context(item, bundle.person, bundle.person_en):
                continue
            excerpt = context_excerpt(item, bundle.person, bundle.person_en)
            if excerpt:
                item.speaker_source_excerpt = excerpt
                item.speaker_context_diagnostic = 'source_identity_bound'
                continue
            if (urlsplit(item.url).hostname not in _HOSTS
                    and item.source.casefold() not in _SOURCE_LABELS
                    and item.source.casefold() not in {host.removeprefix('www.') for host in _HOSTS}):
                item.speaker_context_diagnostic = 'unsupported_publisher'
                continue
            key = (item.url, item.title, item.source)
            if key not in cache:
                if len(cache) >= 3:
                    item.speaker_context_diagnostic = 'context_request_limit'
                    continue

                def fetch_one(item=item):
                    try:
                        resolved = _resolve(item.url)
                        html = _fetch(resolved)
                        body = _body(html, item.title, item.source)
                        return {'source_body': body, 'context_url': resolved,
                                'source_published_at': _published_at(html, item.title, item.source),
                                'context_fetched_at': datetime.now(UTC).isoformat()}
                    except (ValueError, requests.RequestException):
                        return {'speaker_context_diagnostic': 'source_context_unavailable'}

                cache[key] = budget.call(fetch_one, seconds=12,
                                        fallback=lambda: {'speaker_context_diagnostic': 'context_timeout'})
            for field, value in cache[key].items():
                setattr(item, field, value)
            excerpt = context_excerpt(item, bundle.person, bundle.person_en)
            if excerpt:
                item.speaker_source_excerpt = excerpt
                item.speaker_context_diagnostic = 'source_identity_bound'
            elif not getattr(item, 'speaker_context_diagnostic', ''):
                item.speaker_context_diagnostic = 'source_identity_unresolved'
            logger.info('news.speaker_context status=%s', item.speaker_context_diagnostic)
