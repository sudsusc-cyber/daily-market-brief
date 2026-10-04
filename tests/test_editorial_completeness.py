"""Section-specific excerpts and bounded watchpoints keep the original facts."""
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from src.collectors.macro_news import MacroNewsItem
from src.processors.editorial_evidence import analysis_source
from src.processors.holdings_intro import _signal_errors
from src.processors.news_selection import company_candidate, factual_excerpt
from src.processors.thesis.renderer import _rule_for
from src.processors.translation_guard import translation_errors


@pytest.mark.parametrize('summary', [
    'The group will deploy 100 million barrels from existing reserves over the next four months.',
    'The government will release 25 million barrels over six weeks under its existing commitments.',
    'Central banks kept policy rates unchanged after consumer inflation reached 3.2% in September.',
])
def test_macro_summary_does_not_need_a_company_operating_verb(summary):
    item = MacroNewsItem('Governments respond to energy and inflation pressure',
                         datetime(2026, 10, 3, tzinfo=UTC), 'https://example.com/report', 'CNBC',
                         summary=summary)
    assert factual_excerpt(item) == summary


def test_macro_excerpt_still_rejects_truncated_or_old_events():
    item = MacroNewsItem('Governments respond to supply pressure',
                         datetime(2026, 10, 3, tzinfo=UTC), 'https://example.com/report', 'CNBC',
                         summary='The government released reserves on May 6, 2026.')
    assert factual_excerpt(item) == ''
    item.summary = 'The government will release reserves over the next...'
    assert factual_excerpt(item) != item.summary


@pytest.mark.parametrize('prefix', ['HigherVisibility Analysis:', 'Policy Institute Analysis:', 'Opinion:', '评论：'])
def test_explicit_analysis_genre_is_not_reported_operating_evidence(prefix):
    assert analysis_source(prefix + ' Google updated privacy permissions')


@pytest.mark.parametrize('title', [
    'Google updates data permissions for users',
    'Microsoft launched its analysis platform',
    'Apple reported operating results',
])
def test_analysis_as_product_noun_does_not_relabel_a_report(title):
    assert not analysis_source(title)


@pytest.mark.parametrize('text', [
    '甲企业已具备大额买入条件，乙企业具备定投的条件。',
    '甲企业具备既定的大额买入条件，乙企业仍满足定投标准。',
])
def test_current_conditions_are_allowed_without_inventing_a_new_trigger(text):
    rows = [SimpleNamespace(holding=SimpleNamespace(name=name), signal=kind, error=None)
            for name, kind in [('甲企业', 'LUMP_SUM'), ('乙企业', 'DCA')]]
    assert not _signal_errors(text, rows, [])
    assert _signal_errors(text.replace('具备', '首次具备', 1), rows, [])


@pytest.mark.parametrize('original,translation,key', [
    ('Apple updated software privacy permissions.', '苹果更新软件隐私权限。', 'product-data-governance'),
    ('Microsoft changed data access permissions.', '微软修改数据访问权限。', 'product-data-governance'),
    ('Apple plans to update software to notify users whenever agents access their system data.', '苹果计划更新软件，在智能体访问用户系统数据时通知用户。', 'product-data-governance'),
    ('Apple offered replacements for devices that lost service.', '苹果为失去服务的设备提供更换。', 'product-reliability'),
    ('Apple says some users must replace phones after a service-loss bug.', '苹果称部分用户在服务中断故障后必须更换手机。', 'product-reliability'),
    ('Microsoft services suffered outages.', '微软服务出现中断。', 'product-reliability'),
])
def test_watchpoints_bind_both_original_event_and_visible_translation(original, translation, key):
    row = dict(excerpt=original, output_text=translation)
    assert _rule_for(row).key == key
    assert _rule_for(dict(row, output_text='微软公布收入增长10%。')) is None
    assert _rule_for(dict(row, excerpt='A company discussed general industry trends.')) is None


def company_item(title, summary='', ticker='AAPL'):
    return SimpleNamespace(title=title, summary=summary, holding_ticker=ticker,
                           source='Yahoo', url='https://example.com/report',
                           published_at=datetime(2026, 10, 4, tzinfo=UTC))


@pytest.mark.parametrize('title,ticker', [
    ("A shopper's boat ride to Costco saves her hundreds on groceries", 'COST'),
    ('Dear Apple Stock Fans, Mark Your Calendars for October 13', 'AAPL'),
    ('Morgan Stanley reinstates Nvidia as a Top Pick on AI capacity expansion', 'NVDA'),
])
def test_consumer_colour_teasers_and_broker_recommendations_do_not_fill_company_slots(title, ticker):
    assert not company_candidate(company_item(title, ticker=ticker), ticker)


def test_teaser_is_replaced_with_its_real_operating_report():
    summary = 'Apple is preparing a notable smart-home expansion for October 13.'
    item = company_item('Dear Apple Stock Fans, Mark Your Calendars for October 13', summary)
    assert company_candidate(item, 'AAPL')
    assert factual_excerpt(item) == summary


def test_broker_opinion_cannot_displace_an_operating_fact_in_same_summary():
    first = 'Nvidia is supplying chips under a new agreement with a cloud customer.'
    opinion = 'Morgan Stanley reinstated Nvidia as a Top Pick, citing AI capacity expansion.'
    item = company_item('Nvidia supplies cloud customer', first + ' ' + opinion, 'NVDA')
    assert factual_excerpt(item) == first


@pytest.mark.parametrize('source,translation', [
    ('The producers agreed to keep oil output targets steady in November.', '产油国同意在11月维持石油产量目标不变。'),
    ('The buildup raises escalation risk.', '军事集结加大升级风险。'),
    ('Microsoft plans infrastructure spend as non-cancellable commitments mount.', '微软计划支出用于基础设施，不可取消的承诺不断增加。'),
    ('The conditions include a halt to aggression and the release of Iranian assets.', '条件包括停止侵略行为和释放伊朗资产。'),
    ('The court ordered the release of frozen funds.', '法院命令释放冻结资金。'),
])
def test_bilingual_event_families_accept_equivalent_financial_and_policy_actions(source, translation):
    assert not translation_errors(source, translation)


@pytest.mark.parametrize('translation', [
    '法院命令发布新产品。', '法院拒绝释放冻结资金。', '法院命令继续冻结资金。',
])
def test_asset_release_cannot_become_a_product_launch_denial_or_freeze(translation):
    assert translation_errors('The court ordered the release of frozen funds.', translation)


def test_noncancellable_commitment_cannot_become_cancellation_or_lose_restriction():
    source = 'Microsoft plans infrastructure spend as non-cancellable commitments mount.'
    assert translation_errors(source, '微软计划支出用于基础设施，承诺不断增加。')
    assert translation_errors(source, '微软计划支出用于基础设施，取消承诺。')


def test_figure_selection_receives_nickname_and_configured_canonical_identity():
    from src.collectors.figures import FigureBundle, FigureMention
    from src.processors.figure_filter import filter_one

    calls = []
    def chat(payload, **kwargs):
        calls.append(kwargs['task_extra'])
        return SimpleNamespace(text='▦ 1 | no | 2 | 普通观点')
    bundle = FigureBundle(person='皮叉', person_en='Sundar Pichai', query='Sundar Pichai', items=[
        FigureMention('Sundar Pichai says cloud demand is strong', '',
                      datetime(2026, 10, 4, tzinfo=UTC), 'https://example.com/report', 'Reuters')])
    filter_one(bundle, client=SimpleNamespace(chat=chat))
    assert calls and all('皮叉（Sundar Pichai）' in prompt for prompt in calls)
