from types import SimpleNamespace

import pytest

from src.processors.macro_filter import _rebuild_safe_html
from src.processors.news_presentation import present, replay_presentation
from src.processors.thesis.extractor import _verified_grounding_row


@pytest.mark.parametrize('country', ['美国', '英国', '德国', '日本'])
def test_complete_yield_record_variants_share_display_and_all_evidence(country):
    facts = [f'{country}30年期国债收益率升至2002年以来最高。',
             f'30 年期{country}国债收益率攀升至 2002 年以来最高水平。']
    rows = [SimpleNamespace(title=t, summary='', url=f'https://example.com/{i}', source='Source')
            for i, t in enumerate(facts)]
    evidence = []
    html, notes = _rebuild_safe_html('<p>债券市场。[1][2]</p>', rows, evidence)
    assert html.count('data-macro-fact=') == 1 and len(notes) == 2
    obj = SimpleNamespace(summary_html=html, footnotes=notes)
    assert all(_verified_grounding_row(obj, e) for e in evidence)


@pytest.mark.parametrize('different', [
    '美国10年期国债收益率升至2002年以来最高。',
    '美国30年期国债收益率升至2003年以来最高。',
    '日本30年期国债收益率升至2002年以来最高。',
    '美国30年期国债收益率降至2002年以来最低。',
    '美国30年期国债收益率可能升至2002年以来最高。',
    '美国30年期国债收益率未升至2002年以来最高。',
    '美国30年期国债收益率升至5%，创2002年以来最高。',
])
def test_yield_updates_and_qualifiers_remain_different(different):
    assert present(different).text != present('美国30年期国债收益率升至2002年以来最高。').text


@pytest.mark.parametrize('name,ticker', [('腾讯控股', '00700'), ('泡泡玛特', '09992')])
def test_hk_listing_requires_configured_issuer_identity(name, ticker):
    assert present(f'{name}({ticker})回购股份。').text == f'{name}回购股份。'
    assert f'({ticker})' in present(f'其他公司({ticker})发布产品。').text
    assert '(2026)' in present(f'{name}(2026)发布产品。').text


@pytest.mark.parametrize('publisher', ['TradingKey', 'Example News', '新媒体'])
def test_publisher_suffix_after_complete_percentage_is_metadata(publisher):
    raw = f'订阅收入占比不足20% {publisher}'
    assert present(raw, source_name=publisher).text == '订阅收入占比不足20%'
    assert present(f'公司与{publisher}合作。', source_name=publisher).text == f'公司与{publisher}合作。'
    assert replay_presentation(raw, {'presentation_version': 3, 'source_name': publisher}) == raw


def test_old_presentation_contract_remains_replayable():
    raw = '30 年期美国国债收益率攀升至 2002 年以来最高水平。'
    assert replay_presentation(raw, {'presentation_version': 3}) == raw
    assert present(raw).text == '美国30年期国债收益率升至2002年以来最高。'
