from types import SimpleNamespace

import pytest

from src.processors.investment_relevance import long_term_noise_reason
from src.processors.news_selection import company_candidate
from src.processors.translation_guard import translation_errors


@pytest.mark.parametrize('name', ['Microsoft', 'Acme', 'Northstar'])
def test_empty_dividend_commentary_does_not_gain_substance_from_market_cap(name):
    text = f'{name} can afford its dividend today. The investment question is how much room remains. At September 30’s approximate $3.81 trillion market cap […]'
    assert long_term_noise_reason('Can the company grow its dividend?', text)
    assert long_term_noise_reason('Dividend update', f'{name} cut another quarterly check, but the real story is what it reveals after 64 consecutive years of dividend raises.')


@pytest.mark.parametrize('report', [
    'Acme raised its dividend.', 'Acme suspended its dividend.',
    'Acme declared a dividend of $0.50 per share.',
    'Acme reported cash flow of $5 billion.',
])
def test_substantive_financial_reporting_is_not_filtered_with_commentary(report):
    assert not long_term_noise_reason('Acme can afford its dividend today.', report)


def test_real_company_candidate_path_rejects_thin_commentary():
    item = SimpleNamespace(title='Can Microsoft grow its dividend?', summary='Microsoft can afford its dividend today.',source='News')
    assert not company_candidate(item,'MSFT')


@pytest.mark.parametrize('label,zh', [('FULL INTERVIEW','完整采访'),('EXCLUSIVE REPORT','独家报道'),('LIVE COVERAGE','现场报道'),('BREAKING NEWS','突发新闻')])
def test_editorial_format_label_is_translatable_but_model_identifier_is_not(label,zh):
    original = f'{label}: Acme says AGI is already here.'
    translated = f'{zh}：Acme 称 AGI 已经到来。'
    assert not translation_errors(original,translated)
    assert 'identifier:AGI' in translation_errors(original,translated.replace('AGI','XYZ'))


def test_label_words_in_entity_position_are_still_protected():
    assert 'identifier:FULL' in translation_errors('FULL announces a new model.', 'Acme 宣布一个新模型。')


def test_media_credit_cleanup_retains_an_exact_verifiable_source_span():
    from src.processors.news_selection import factual_excerpt
    from src.processors.source_grounding import source_sentences
    title = 'FULL INTERVIEW: Acme CEO says AGI is already here | Channel Name (aSeHSBY1Dk) - Publisher'
    item = SimpleNamespace(title=title,summary='',source='Publisher')
    excerpt = factual_excerpt(item)
    assert excerpt == 'Acme CEO says AGI is already here'
    assert excerpt in title and excerpt in source_sentences(item)
    item.title = 'FULL INTERVIEW: Acme CEO says AGI is already here | Critics disagree'
    assert 'Critics disagree' in factual_excerpt(item)


@pytest.mark.parametrize('title', ['恒指创三月来最大跌幅，腾讯阿里均跌2%。','纳指下挫，科技股齐跌3%。','某公司股价上涨。'])
def test_price_only_roundup_needs_operating_disclosure(title):
    assert long_term_noise_reason(title)
    assert not long_term_noise_reason(title,'公司公布营收增长20%。')
