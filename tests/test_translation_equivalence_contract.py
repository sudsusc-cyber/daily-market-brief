"""Replay real rejected translations, then mutate their material facts."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.processors.news_selection import factual_excerpt, publishable_excerpt
from src.processors.translation_guard import translation_errors

ROWS = json.loads((Path(__file__).parent / 'fixtures/september30_translation_rejections.json').read_text())


@pytest.mark.parametrize('row', ROWS, ids=lambda row: row['title'][:60])
def test_recorded_equivalent_translation_survives(row):
    assert translation_errors(row['original'], row['translated']) == []


@pytest.mark.parametrize('original,translated', [
    ('Revenue rose in September.', '营收在八月增长。'),
    ('Sales rise 10.7% in Q4.', '销售第三季度增长10.7%。'),
    ('Google challenges an EU order.', 'Google 对抗欧洲央行命令。'),
    ('Acme may launch a service.', 'Acme 已推出服务。'),
    ('Acme has paid $10 million.', 'Acme 将支付1000万美元。'),
    ('Acme plans to invest over US$10b.', 'Acme 计划投资100亿美元。'),
    ('Acme has more than two decades of partnership.', 'Acme 有不足二十年的合作。'),
    ('Central bank holds interest rates.', '央行上调利率。'),
    ('Central bank raises interest rates.', '央行维持利率。'),
    ('Central bank raises interest rates.', '央行未上调利率。'),
    ('Acme was not approved by regulators.', 'Acme 已获监管批准。'),
    ('Acme expands its payment services.', 'Acme 缩减支付服务。'),
    ('Acme releases GPT-6.', 'Acme 发布 GPT-7。'),
    ('Acme releases XYZ software.', 'Acme 发布 ABC 软件。'),
    ('Acme launches on January 12, 2027.', 'Acme 于2027年1月13日推出。'),
    ('Acme announces $10 million in revenue.', 'Acme 宣布1000万港元营收。'),
    ('Revenue rose 10% while EPS fell 2%.', '营收下降2%，每股收益增长10%。'),
])
def test_equivalence_does_not_allow_material_fact_changes(original, translated):
    assert translation_errors(original, translated)


@pytest.mark.parametrize('original,translated', [
    ('Troops march across the border.', '部队越过边境行军。'),
    ('Sales rise in March.', '销售在三月增长。'),
    ('Acme fans attend a conference.', 'Acme 粉丝参加一场会议。'),
    ('Acme expands its paid services.', 'Acme 扩展付费服务。'),
    ('Acme has paid $10 million.', 'Acme 已支付1000万美元。'),
    ('Acme may launch a service.', 'Acme 可能推出服务。'),
    ('Acme exceeds May estimates.', 'Acme 超过五月估算。'),
    ('Sales rise in October.', '销售在十月增长。'),
    ('Acme launches a service in Q1.', 'Acme 第一季度推出服务。'),
    ('Acme maintains guidance.', 'Acme 维持指引。'),
    ('Acme protects platform control.', 'Acme 维持平台控制权。'),
    ('ECB holds interest rates.', '欧洲央行维持利率。'),
    ('EU announces a policy.', '欧盟宣布一项政策。'),
    ('Acme has more than two decades of partnership.', 'Acme 有二十多年的合作。'),
    ('Acme will pay $1 billion over seven years.', 'Acme 将在七年内支付10亿美元。'),
])
def test_grammar_and_semantic_context_are_not_literal_token_checks(original, translated):
    assert translation_errors(original, translated) == []


def test_translation_success_does_not_waive_model_scope_check():
    row = next(row for row in ROWS if row['title'].startswith('OpenAI pulls'))
    item = SimpleNamespace(title=row['original'], summary='', url='https://example.com/news', source='Source')
    # A correct translation does not supply the missing activity/affected scope.
    assert not publishable_excerpt(item, row['original'])


@pytest.mark.parametrize('publisher', ['Wells Fargo', 'Acme Research', 'Northstar'])
def test_analyst_winner_opinion_does_not_replace_a_company_event(publisher):
    title = f'{publisher} Finds Unexpected Winners in the Semiconductor Race'
    item = SimpleNamespace(title=title, summary='', source='Source')
    assert factual_excerpt(item) == ''
    fact = 'Acme reported revenue growth of 10% in Q4.'
    item.summary = fact
    assert factual_excerpt(item) == fact


def test_actual_contract_winner_remains_eligible():
    fact = 'Acme wins a $10 million cloud contract.'
    assert factual_excerpt(SimpleNamespace(title=fact, summary='', source='Source')) == fact
