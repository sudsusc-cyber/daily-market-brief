from types import SimpleNamespace

import pytest

from src.processors.investment_relevance import long_term_noise_reason
from src.processors.news_selection import company_candidate, complete_excerpt


@pytest.mark.parametrize('text', [
    '实拍纪念品商店文创，赛场内外运动员背包上的泡泡玛特挂件亮相。',
    '街拍：路人携带苹果设备和品牌背包。',
    'Actor seen carrying a Microsoft tablet on the red carpet',
    'Street style: Apple logo bags at the festival',
])
def test_incidental_brand_sightings_are_not_operating_news(text):
    assert long_term_noise_reason(text) == 'incidental_brand_appearance'


def test_reported_commercial_impact_survives_appearance_context():
    assert not long_term_noise_reason('赛场内外品牌亮相', '公司签署五年授权协议，新增授权收入为300万元。')


def test_incidental_item_cannot_enter_company_body_and_thesis_evidence():
    text='实拍名古屋亚运纪念品商店多款亚运周边。赛场内外，运动员背包上的泡泡玛特挂件、华为设备亮相。小小'
    item=SimpleNamespace(title=text, summary='', holding_ticker='9992.HK', source='News')
    assert not company_candidate(item, '9992.HK')
    assert not complete_excerpt(text)


@pytest.mark.parametrize('text', ['品牌亮相。小小', '新品上市。种种。', '上市。纷纷'])
def test_bare_reduplicated_tail_is_not_a_complete_sentence(text):
    assert not complete_excerpt(text)


def test_reduplicated_word_inside_complete_sentence_is_preserved():
    assert complete_excerpt('小小的零件产生了重大影响。')
