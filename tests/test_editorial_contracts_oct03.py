"""Publication contracts: accept paraphrases, reject changed facts, preserve coverage."""
from datetime import UTC, datetime

import pytest

from src.collectors.company_news import NewsItem
from src.processors.macro_events import edition_events
from src.processors.news_presentation import present, replay_presentation
from src.processors.news_selection import factual_excerpt, publishable_excerpt
from src.processors.translation_guard import translation_errors


@pytest.mark.parametrize('source,good,bad', [
    ('Acme is not intimidated by custom chips.', 'Acme 不惧定制芯片。', 'Acme 畏惧定制芯片。'),
    ('Acme will spend $100M to train 10,000 engineers.', 'Acme 将投入 1 亿美元培训 10,000 名工程师。', 'Acme 已投入 1 亿美元培训 10,000 名工程师。'),
    ('Jobs increased by 29,000.', '新增就业 29,000 人。', '就业减少 29,000 人。'),
    ('Unemployment rose to 4.2%.', '失业率升至 4.2%。', '失业率降至 4.2%。'),
    ('Inflation is coming down.', '通胀正在回落。', '通胀正在上涨。'),
])
def test_same_meaning_and_changed_meaning(source, good, bad):
    assert not translation_errors(source, good)
    assert translation_errors(source, bad)


def item(title, summary=''):
    return NewsItem(title, datetime(2026,10,3,tzinfo=UTC), 'https://example.com/news', 'Source', summary, 'NVDA')


@pytest.mark.parametrize('title', [
    'Nvidia stock nears a record high',
    'The Nvidia era needs more than continuity',
    'A look back at Nvidia investment decades ago',
    'Nvidia puts $500 billion on the table',
])
def test_noise_or_ambiguous_amount_cannot_be_the_published_excerpt(title):
    news = item(title)
    assert not publishable_excerpt(news,title)
    assert not factual_excerpt(news)
    news.summary = 'Nvidia signed a supply contract with a hospital.'
    assert factual_excerpt(news) == news.summary


def test_explicit_monetary_commitment_remains_eligible():
    news = item('Nvidia committed $500 million to build a factory.')
    assert factual_excerpt(news) == news.title


def test_release_and_policy_response_share_one_topic_despite_future_policy_month():
    events = edition_events(['US payrolls increased by 29000 in September.',
                             'US traders see little chance of an October Fed interest rate hike after weak jobs data.'])
    assert events[0].topic == events[1].topic == '就业市场'
    different = edition_events(['US payrolls increased by 29000 in September.',
                               'US traders see little chance of an October Fed interest rate hike after weak August jobs data.'])
    assert different[0].topic != different[1].topic


def test_technical_prose_is_versioned_and_does_not_change_numbers():
    text = 'Google 的 tensor processing unit 收入预计为 1040 亿美元。'
    output = present(text).text
    assert '张量处理器' in output and '1040' in output
    assert replay_presentation(text, {'presentation_version':12}) == text
    assert replay_presentation(text, {'presentation_version':13}) == output


def test_financial_idiom_requires_natural_translation_without_inventing_fact():
    assert translation_errors('Investors try to make peace with bond losses.', '投资者试图与债券亏损和解。')
    assert not translation_errors('Investors try to make peace with bond losses.', '投资者试图适应债券亏损。')


def test_correct_number_cannot_change_economic_meaning():
    assert 'economic_amount_role' in translation_errors('Acme has $500 billion in assets under management.', 'Acme 投入 5000 亿美元。')
    assert not translation_errors('Acme has $500 billion in assets under management.', 'Acme 的管理资产为 5000 亿美元。')


def test_forecasts_remain_useful_without_becoming_realised_evidence():
    from datetime import date

    from src.processors.thesis.renderer import _publication_item
    row = {'excerpt':'Analysts forecast Google revenue of $104 billion by 2028.',
           'output_text':'分析师预测 Google 到 2028 年收入为 1040 亿美元。',
           'original_title':'Analysts forecast Google revenue of $104 billion by 2028.',
           'original_summary':'', 'published_at':'2026-10-03', 'url':'https://example.com/forecast'}
    result, reason = _publication_item('company_news', row, date(2026,10,3))
    assert result, reason
    assert result['marker'] == '预期变化'
    assert result['evidence_type'] == 'forecast'
    assert '兑现' in result['watch']


def test_macro_shared_cause_is_compact_and_replayable():
    from src.processors.macro_filter import MacroNewsSummary, _rebuild_safe_html
    from src.processors.thesis.extractor import _verified_grounding_row
    texts = ['油价下跌，因 G7 国家将释放柴油储备，据报道沙特计划袭击。', 'G7 国家将释放柴油储备，因欧洲和中东的战争制约了燃料供应。']
    items=[]
    for i,text in enumerate(texts):
        source=item(text)
        source.holding_ticker=None
        source.url=f'https://example.com/{i}'
        items.append(source)
    evidence=[]
    html,notes=_rebuild_safe_html('<p>[1][2]</p>',items,evidence)
    assert html.count('G7 国家将释放柴油储备') == 1
    assert '相关背景是欧洲和中东的战争制约了燃料供应' in html
    summary=MacroNewsSummary(html,notes,evidence)
    assert all(_verified_grounding_row(summary,row) for row in evidence)
    summary.summary_html=html.replace('G7 国家将释放柴油储备','某项安排')
    assert not _verified_grounding_row(summary,evidence[1])


@pytest.mark.parametrize('different', ['G7 国家将释放汽油储备', 'G7 国家已释放柴油储备', 'G7 国家将释放 200 万桶柴油储备'])
def test_macro_context_does_not_elide_changed_object_state_or_amount(different):
    from src.processors.news_presentation import macro_context_text
    text = different + '，因供应受限。'
    assert macro_context_text(text, 'G7 国家将释放柴油储备') == text
