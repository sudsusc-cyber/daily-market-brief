from datetime import UTC, datetime

import pytest

from src.collectors.macro_news import MacroNewsItem
from src.processors.editorial_evidence import editorial_issue
from src.processors.news_selection import factual_excerpt, publishable_excerpt
from src.processors.source_grounding import grounded_text, source_sentences


@pytest.mark.parametrize('text', [
    'Plus, see how a reply-all email after Oct. 7 tore a business apart, and what Oura’s stalled IPO reveals about one-hit wonders.',
    '此外，看看 10 月 7 日之后的一封回复全部邮件如何撕裂一家企业，以及 Oura IPO 的其他故事。',
    'Also read about the other stories in today’s newsletter.',
    "Plus, here's how another company changed its strategy.",
])
def test_digest_recommendations_are_reader_navigation(text):
    assert editorial_issue(text) == 'reader_navigation'


def test_actual_wsj_teaser_cannot_replace_its_france_headline():
    title = 'France Spent for Years. Now the Bill Is Coming Due.'
    teaser = 'Plus, see how a reply-all email after Oct. 7 tore a business apart, and what Oura’s stalled IPO reveals about one-hit wonders.'
    item = MacroNewsItem(title, datetime(2026, 10, 6, tzinfo=UTC), 'https://example.com/france', 'WSJ', summary=teaser)
    item.source_excerpt = teaser
    item.translated_excerpt = '此外，看看 10 月 7 日之后的一封回复全部邮件如何撕裂一家企业，以及 Oura IPO 的其他故事。'
    assert factual_excerpt(item) == title
    assert teaser not in source_sentences(item)
    assert not grounded_text(item.translated_excerpt, [item])[0]


def test_trailing_digest_story_is_not_rescued_by_sentence_splitting():
    item = MacroNewsItem('France announces a new budget.', datetime(2026, 10, 6, tzinfo=UTC), 'https://example.com/budget', 'WSJ',
                        summary='Plus, see our other stories. Microsoft reports revenue growth of 10%.')
    assert not publishable_excerpt(item, 'Microsoft reports revenue growth of 10%.')
    assert factual_excerpt(item) == item.title


def test_also_with_a_real_reporting_predicate_remains_eligible():
    text = 'Also, Microsoft reported revenue growth of 10%.'
    item = MacroNewsItem('Microsoft earnings', datetime(2026, 10, 6, tzinfo=UTC), 'https://example.com/earnings', 'Source', summary=text)
    assert editorial_issue(text) is None
    assert publishable_excerpt(item, text)
