"""Actual RSS wrappers must not act as quotes or leak publisher metadata."""
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.collectors.figures import FigureBundle, FigureMention
from src.processors.figure_filter import _has_quote_marker, filter_one
from src.processors.news_presentation import publication_text
from src.processors.source_grounding import grounded_text

NOW=datetime(2026,9,29,tzinfo=UTC)


@pytest.mark.parametrize('title', [
    'Nvidia says its new platform can prevent AI agents from going rogue - Forbes Australia',
    'Nvidia said its new platform can prevent AI agents from going rogue',
    'Microsoft announced a new cloud product',
    '英伟达表示将投资100亿美元建设数据中心',
    'A new platform for AI agents',
])
def test_rss_attributes_and_company_statements_do_not_qualify_as_person_quotes(title):
    snippet=f'<a href="https://example.com/a-very-long-link" target="_blank">{title}</a>&nbsp;&nbsp;<font color="#6f6f6f">Forbes Australia</font>'
    item=FigureMention(title,snippet,NOW,'https://example.com/quote','Forbes Australia')
    assert not _has_quote_marker(item)
    llm=Mock()
    result=filter_one(FigureBundle('黄仁勋','Jensen Huang','Jensen Huang',[item]),client=llm)
    assert not result.items and not result.error
    llm.chat.assert_not_called()


@pytest.mark.parametrize('title,snippet', [
    ('Jensen Huang said inference demand would grow', ''),
    ('Jensen Huang says inference demand will grow', ''),
    ('黄仁勋公开表示将增加资本开支', ''),
    ('Jensen Huang on demand', '<p>“Inference demand will grow significantly,” he said.</p>'),
    ('Jensen Huang: &quot;Inference demand will grow significantly&quot;', ''),
    ('Nvidia CEO said inference demand would grow', ''),
])
def test_actual_rendered_quotes_and_person_statements_still_qualify(title,snippet):
    assert _has_quote_marker(FigureMention(title,snippet,NOW,'https://example.com/quote','Reuters'))


@pytest.mark.parametrize('separator', [' - ', '——', '\u00a0\u00a0', '  ', '\n\n'])
def test_known_publisher_tail_removed_without_mutating_evidence(separator):
    source='washingtonpost.com'
    original='OpenAI cancels new AI launch, citing safety issues'+separator+source
    translated='OpenAI 以安全问题为由取消新的 AI 发布'+separator+source
    item=SimpleNamespace(title=original,summary='',url='https://example.com/ai',source=source,
                         source_excerpt=original,translated_excerpt=translated,published_at=NOW)
    text,mapping=grounded_text(translated,[item])
    assert text=='OpenAI 以安全问题为由取消新的 AI 发布'
    assert mapping[0]['original_title']==original and mapping[0]['validated_text']==translated
    assert mapping[0]['source_name']==source


@pytest.mark.parametrize('text,source', [
    ('Reuters 报道，微软维持计划。','Reuters'),
    ('用户访问 washingtonpost.com','washingtonpost.com'),
    ('公司与 Reuters 签订协议。','Reuters'),
    ('公司公布新产品  Actual Partner','Reuters'),
])
def test_attribution_or_named_object_inside_prose_is_not_a_publisher_tail(text,source):
    assert publication_text(text,source_name=source)==text.replace('  ',' ')
