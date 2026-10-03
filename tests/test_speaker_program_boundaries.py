from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from src.collectors.figures import FigureMention
from src.processors.figure_filter import _has_quote_marker
from src.processors.news_presentation import macro_context_text
from src.processors.news_selection import factual_excerpt, publishable_excerpt


@pytest.mark.parametrize('company',['Anthropic','Microsoft','Acme Inc.'])
def test_company_document_is_not_a_persons_warning(company):
    item=FigureMention(f'{company} warns of risks in its own IPO filing','',datetime.now(UTC),'https://example.com','News')
    assert not _has_quote_marker(item)
    item.title=f'{company} CEO warns of risks in an IPO filing'
    assert _has_quote_marker(item)


def test_named_person_statement_survives_document_context():
    item=FigureMention('Dario Amodei warns of risks in an IPO filing','',datetime.now(UTC),'https://example.com','News')
    assert _has_quote_marker(item)


def test_program_guest_credit_cannot_rescue_an_empty_interview_topic():
    item=SimpleNamespace(title='Former Labor Secretary on Jobs Report',summary='He speaks with Jane Doe on Market TV’s "Closing Hour." (Source: Market TV)',source='Market TV')
    assert not publishable_excerpt(item,'He speaks with Jane Doe on Market TV’s "Closing Hour." (Source: Market TV)')
    assert not factual_excerpt(item)
    assert publishable_excerpt(item,'The minister speaks with the president on trade sanctions.')


def test_elision_does_not_leave_a_subordinate_fragment():
    fact='G7 国家将释放柴油库存'
    assert macro_context_text('随着欧洲和中东的战争制约燃料供应，'+fact,fact)=='欧洲和中东的战争制约燃料供应'
