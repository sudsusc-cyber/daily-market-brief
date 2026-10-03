from types import SimpleNamespace

import pytest

from src.processors.news_selection import macro_candidate
from src.processors.translation_guard import translation_errors


@pytest.mark.parametrize('country,short', [('China','华'),('Japan','日'),('Germany','德')])
def test_direction_compound_preserves_country_in_trade_policy(country,short):
    en=f'Acme warns excessive {country} export controls could create rival'
    zh=f'Acme 警告对{short}出口管制过度可能催生竞争对手'
    assert not translation_errors(en,zh)
    assert 'localized_entity_binding' in translation_errors(en,zh.replace('对'+short,'对美'))


def test_statistical_release_is_not_product_launch():
    original='Nonfarm payrolls increased by 29,000 in September after downward revisions to the prior two months, according to Bureau of Labor Statistics data released Friday.'
    translated='美国劳工统计局周五发布的数据显示，9 月非农就业人数增加 29,000，此前两个月数据被下修。'
    assert not translation_errors(original,translated)
    assert translation_errors(original,translated.replace('29,000','39,000'))
    assert 'launch' in translation_errors('Acme released a new data platform.', 'Acme 公布数据。')


@pytest.mark.parametrize('pause', ['rest breaks','coffee breaks','private downtime'])
def test_summit_schedule_colour_is_not_a_policy_event(pause):
    item=SimpleNamespace(title=f'Unusual {pause} consumed summit hours',summary='The leader had private time.')
    assert not macro_candidate(item)
    item.summary='Leaders announced new trade sanctions.'
    assert macro_candidate(item)
