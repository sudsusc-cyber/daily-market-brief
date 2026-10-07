import pytest

from src.processors.macro_topics import macro_topic, macro_topics
from src.processors.news_presentation import present, publication_text, replay_presentation


@pytest.mark.parametrize('text', [
    'S&P 500 hits record high as AI stocks shrug off bond market slump',
    'S&P 500 创历史新高，AI 股未受债市下跌影响。华尔街基准指数收于新高。',
    'Nasdaq rises after Treasury yields drop.',
    '股市上涨，债券市场下跌。',
    '股市未受债券收益率高企影响上涨。',
])
def test_primary_equity_movement_is_not_background_bond_news(text):
    assert macro_topic(text) == '资本市场'


@pytest.mark.parametrize('text,topic', [
    ('US Treasuries drop while S&P 500 rises.', '美债市场'),
    ('债券市场下跌，标普 500 创新高。', '债券市场'),
    ('Oil stocks fall after supply disruptions.', '能源市场'),
    ('油价下跌，股市反弹。', '能源市场'),
    ('亚洲股市与债市承压，美国国债收益率上行加剧通胀担忧。', '美债市场'),
    ('Asian stock markets and bond markets fall after US Treasury yields rise.', '美债市场'),
    ('欧洲股市受债券收益率高企影响承压。', '债券市场'),
])
def test_bonds_and_physical_inventory_keep_their_actual_primary_topic(text, topic):
    assert macro_topic(text) == topic


def test_equity_events_share_a_topic_without_losing_bond_events():
    assert macro_topics(['S&P 500 hits record high.', 'Nasdaq rises.', 'US Treasuries drop.']) == ['资本市场', '资本市场', '美债市场']


def test_houthi_display_name_is_localized_without_changing_archived_replay():
    text = '伊朗支持的 Houthis 袭击沙特阿拉伯。'
    assert publication_text(text) == '伊朗支持的胡塞武装袭击沙特阿拉伯。'
    assert present(text, _version=18).text == text
    assert replay_presentation(text, {'presentation_version': 18}) == text
