"""Positive publication controls alongside factual mutation controls."""
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.processors.news_selection import factual_excerpt, noun_fragment
from src.processors.source_grounding import grounded_text
from src.processors.translation_guard import translation_errors


@pytest.mark.parametrize('owner', ['OpenAI', 'Acme', 'Northstar'])
@pytest.mark.parametrize('predicate', [
    'obscured hacking activity in government site breaches',
    'detected previously unknown security flaws',
    'outperformed competing systems in an independent evaluation',
    'withstood an external security assessment',
    'leaked private documents during testing',
])
def test_unlisted_predicate_does_not_erase_complete_news(owner, predicate):
    title = f'{owner}’s agents {predicate}'
    item = SimpleNamespace(title=title, summary='', source='Source', holding_ticker=None,
                           published_at=datetime(2026, 9, 30, tzinfo=UTC))
    assert not noun_fragment(title)
    assert factual_excerpt(item) == title


@pytest.mark.parametrize('text', [
    'Acme Model Defeats Rival Platform',
    '某公司的模型识别出此前未知的漏洞。',
    '某公司的智能体泄露了测试文档。',
    'The platform withstood the attack.',
])
def test_absence_of_known_event_is_not_evidence_of_a_fragment(text):
    assert not noun_fragment(text)


@pytest.mark.parametrize('a,b,za,zb', [
    ('Texas', 'Arizona', '得克萨斯州', '亚利桑那州'),
    ('France', 'Italy', '法国', '意大利'),
    ('Japan', 'India', '日本', '印度'),
])
def test_background_clause_can_move_without_changing_amount_owner(a, b, za, zb):
    source = f'Acme considers {a} investment on top of a $265 billion {b} investment'
    good = f'Acme 考虑在 2650 亿美元 {zb} 投资之外再投资 {za}'
    assert not translation_errors(source, good)
    bad = f'Acme 考虑在 2650 亿美元 {za} 投资之外再投资 {zb}'
    assert translation_errors(source, bad)


def test_two_amounts_cannot_trade_locations_when_clauses_move():
    source = 'Acme invests $10 billion in Texas; Acme invests $20 billion in Arizona'
    good = 'Acme 在亚利桑那州投资 200 亿美元；Acme 在得克萨斯州投资 100 亿美元'
    assert not translation_errors(source, good)
    assert 'localized_amount_binding' in translation_errors(source, good.replace('200','TMP').replace('100','200').replace('TMP','100'))


def test_received_valid_translation_and_frontier_story_recover():
    rows = json.loads((Path(__file__).parent / 'fixtures/october1_resend_sources.json').read_text())
    row = rows['company'][2]
    assert not translation_errors(row['excerpt'], row['validated_text'])
    row = rows['frontier'][0]
    item = SimpleNamespace(title=row['original_title'], summary=row['original_summary'],
                           source='Financial Times', holding_ticker=None,
                           published_at=datetime(2026, 9, 30, tzinfo=UTC))
    assert factual_excerpt(item)
    for row, ticker in [(rows['company'][2], 'TSM'), (rows['frontier'][0], None)]:
        item = SimpleNamespace(title=row['original_title'], summary=row['original_summary'],
                               source=row['source_name'], holding_ticker=ticker,
                               published_at=datetime.fromisoformat(row['published_at']),
                               url=row['url'], source_excerpt=row['excerpt'],
                               translated_excerpt=row['validated_text'])
        text, evidence = grounded_text(row['validated_text'], [item])
        assert text and evidence
        assert evidence[0]['original_title'] == row['original_title']


@pytest.mark.parametrize("verb", ["considers", "considered", "is considering"])
def test_consideration_is_tentative_for_all_inflections(verb):
    original = f"Acme {verb} investment in Texas"
    assert not translation_errors(original, "Acme 考虑在得克萨斯州投资")
    assert "modality" in translation_errors(original, "Acme 在得克萨斯州投资")
