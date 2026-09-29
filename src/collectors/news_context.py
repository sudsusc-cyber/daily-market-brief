"""Bounded article enrichment for headline-only AI technique disputes.

Only explicitly supported publisher hosts are fetched. RSS fields are immutable;
article identity and body provenance are retained separately for audit.
"""
import logging
import re
from datetime import UTC, datetime
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup

from src.processors.technical_context import contextual_excerpt, needs_technical_context
from src.utils.news_facts import canonical_fact
from src.utils.runtime_budget import RuntimeBudget

logger = logging.getLogger(__name__)
_HOSTS = {'www.digitaltoday.co.kr', 'digitaltoday.co.kr', 'www.cnbc.com'}
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
    article = soup.select_one('#article-view-content-div, [itemprop="articleBody"], .ArticleBody-articleBody, article')
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


def enrich_technical_context(items):
    """At most 3 sources / 20 seconds total, within the workflow cutoff."""
    budget = RuntimeBudget(seconds=20)
    cache = {}
    attempts = 0
    for item in items:
        if not needs_technical_context(item.title) or contextual_excerpt(item):
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
            item.context_diagnostic = 'verified_context' if contextual_excerpt(item) else 'insufficient_context'
        logger.info('news.context_result status=%s', item.context_diagnostic)
