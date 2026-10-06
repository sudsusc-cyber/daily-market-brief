from types import SimpleNamespace

import pytest

from src.processors.news_selection import complete_excerpt, factual_excerpt
from src.processors.source_grounding import source_sentences
from src.renderer.news_prose import sentence_end


@pytest.mark.parametrize('issuer', ['Groq', 'Acme', 'Example'])
@pytest.mark.parametrize('apostrophe', ["'", '’'])
def test_feed_cut_off_inside_possessive_uses_intact_title(issuer, apostrophe):
    title = f'{issuer} licensing deal faces lawsuit'
    summary = f'The licensing deal is the subject of a lawsuit alleging that {issuer}{apostrophe}'
    item = SimpleNamespace(title=title, summary=summary, source='News')
    assert not complete_excerpt(summary)
    assert summary not in source_sentences(item)
    assert factual_excerpt(item) == title


@pytest.mark.parametrize('text', [
    "Nvidia's licensing deal faces a lawsuit.",
    "Company acquired McDonald's.",
    "Companies protect their customers' interests.",
    "The company supports workers' rights.",
    "He said 'the company is still growing'",
    "He said ‘the company is still growing’",
])
def test_internal_possessives_and_real_closing_quotes_remain_usable(text):
    assert complete_excerpt(text)


@pytest.mark.parametrize('text, expected', [
    ('人们需要“接受一些坏事”', '人们需要“接受一些坏事”。'),
    ('该产品被称为“下一代平台”', '该产品被称为“下一代平台”。'),
    ('他称：“公司仍在增长”', '他称：“公司仍在增长。”'),
    ('他说“尚未批准”', '他说“尚未批准。”'),
    ('“公司仍在增长”', '“公司仍在增长。”'),
    ('他问“是否已批准？”', '他问“是否已批准？”'),
])
def test_sentence_stop_respects_reported_sentence_or_quoted_object(text, expected):
    assert sentence_end(text) == expected
    assert sentence_end(expected) == expected
