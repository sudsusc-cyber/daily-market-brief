from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from src.collectors.company_news import NewsItem
from src.collectors.macro_news import MacroNewsItem
from src.processors.news_selection import factual_excerpt, macro_geography_context
from src.processors.source_grounding import checked_excerpt, grounded_text, source_sentences
from src.processors.thesis.extractor import _verified_grounding_row


def item(country='China'):
    return MacroNewsItem(
        f'{country} closes hundreds of banks to bolster financial system',
        datetime.now(UTC), 'https://example.com/banks', 'Source',
        summary='More than 670 lenders shut down last year as smaller players remain the sector’s weakest part',
    )


@pytest.mark.parametrize('country', ['China', 'Canada', 'Japan', 'United Kingdom', 'India'])
def test_macro_standfirst_retains_exact_original_geography_and_period(country):
    news = item(country)
    raw = news.title + '\n' + news.summary
    assert factual_excerpt(news) == raw
    assert raw in source_sentences(news)
    assert news.title.startswith(country) and 'last year' in news.summary


def test_self_contained_standfirst_is_not_duplicated_with_headline():
    news = item()
    news.summary = 'More than 670 lenders in China shut down last year as smaller players remain the sector’s weakest part'
    assert factual_excerpt(news) == news.summary
    assert macro_geography_context(news) == ''


def test_multiple_jurisdictions_cannot_lose_one_in_standfirst():
    news = item()
    news.title = 'China and Canada agree to reopen bank negotiations'
    news.summary = 'Canada reported that negotiations will resume next month after a pause last year'
    assert factual_excerpt(news) == news.title + '\n' + news.summary


def test_source_bound_context_translation_and_publication_replay():
    news = item()
    news.source_excerpt = factual_excerpt(news)
    translation = '中国关闭数百家银行以巩固金融体系。去年有超过 670 家贷款机构关闭，较小机构仍是该行业最薄弱的部分。'
    news.translated_excerpt = translation
    assert checked_excerpt(news) == (news.source_excerpt, translation)
    output, rows = grounded_text(translation, [news])
    assert output and len(rows) == 1
    assert _verified_grounding_row(SimpleNamespace(text=output), rows[0])
    news.translated_excerpt = translation.replace('中国', '加拿大')
    assert checked_excerpt(news) == ('', '')
    news.translated_excerpt = translation.replace('去年', '今年')
    assert checked_excerpt(news) == ('', '')
    news.translated_excerpt = translation.replace('670', '760')
    assert checked_excerpt(news) == ('', '')


@pytest.mark.parametrize('summary', [
    'More than 670 lenders shut down last year...',
    'Here is why investors should buy these bank stocks now.',
    'More than 670 lenders shut down last year. ' + 'a ' * 500,
])
def test_context_does_not_rescue_incomplete_promotional_or_unbounded_summary(summary):
    news = item()
    news.summary = summary
    assert macro_geography_context(news) == ''


def test_company_slot_does_not_adopt_macro_context_composition():
    macro = item()
    company = NewsItem(macro.title, macro.published_at, macro.url, macro.source, summary=macro.summary)
    assert factual_excerpt(company) != company.title + '\n' + company.summary


@pytest.mark.parametrize('source,translated', [
    ('Lenders closed last year.', '贷款机构去年关闭。'),
    ('The policy applies next month.', '该政策下个月适用。'),
    ('The policy applies this month.', '该政策这个月适用。'),
    ('The policy applied in the previous quarter.', '该政策在上一季度适用。'),
    ('The policy applies next week.', '该政策下星期适用。'),
])
def test_relative_calendar_scope_accepts_equivalent_forms(source, translated):
    from src.processors.translation_guard import translation_errors

    assert not translation_errors(source, translated)


@pytest.mark.parametrize('source,translated', [
    ('Lenders closed last year.', '贷款机构关闭。'),
    ('Lenders closed last year.', '贷款机构今年关闭。'),
    ('Output stays steady next month.', '产量上个月保持稳定。'),
    ('The policy applied in the previous quarter.', '该政策在本季度适用。'),
    ('The policy applies next week.', '该政策上星期适用。'),
])
def test_relative_calendar_scope_rejects_omission_and_reversal(source, translated):
    from src.processors.translation_guard import translation_errors

    assert 'relative_calendar_period' in translation_errors(source, translated)


@pytest.mark.parametrize('text', ['创下一周多以来最大跌幅', '创下一个月来最大涨幅', '创下一年新低'])
def test_record_verb_and_rolling_duration_are_not_future_calendar_period(text):
    from src.processors.translation_guard import _relative_calendar_periods

    assert not _relative_calendar_periods(text)


def test_cached_standfirst_cannot_discard_required_geography():
    news = item()
    news.source_excerpt = news.summary
    news.translated_excerpt = '去年有超过 670 家贷款机构关闭，较小机构仍是该行业最薄弱的部分。'
    assert checked_excerpt(news) == ('', '')
    assert source_sentences(news) == [news.title + '\n' + news.summary]
