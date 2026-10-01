from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from src.collectors.sentiment import SentimentBundle, SentimentMetric
from src.processors.editorial_evidence import editorial_issue
from src.processors.macro_topics import macro_topic
from src.processors.news_presentation import present, replay_presentation, voice_text
from src.processors.news_selection import company_candidate, factual_excerpt
from src.processors.sentiment_judge import _argument_errors
from src.processors.thesis.renderer import _rule_for
from src.processors.translation_guard import translation_errors


@pytest.mark.parametrize('original,translated', [
    ('US watchdog to investigate AI labs for potential consumer harms', '美国监管机构将调查 AI 实验室可能对消费者造成的损害'),
    ('FTC launches broad investigation into Anthropic, OpenAI', 'FTC 对 Anthropic、OpenAI 展开广泛调查'),
    ('Sam Altman declined to testify before the Senate', 'Sam Altman 拒绝在参议院作证'),
    ('Google debuts Gemini 4 Argon, its latest frontier model', 'Google 推出 Gemini 4 Argon，其最新前沿模型'),
    ('Nvidia Authorizes $150B Buyback, Lifting Total to $235B', 'Nvidia 批准1500亿美元回购，使总额升至2350亿美元'),
    ('Asha Sharma says Microsoft does not plan to sell Xbox', 'Asha Sharma 表示 Microsoft 不计划出售 Xbox'),
    ('AMD chief said to visit S. Korea next month', 'AMD 首席执行官据称将于下月访问韩国'),
    ('OpenAI sued over rogue AI attack as Sam Altman says IPO must wait for safer models', 'OpenAI 因失控 AI 攻击被起诉，Sam Altman 称 IPO 必须等待更安全的模型'),
])
def test_correct_event_translations_survive(original, translated):
    assert translation_errors(original, translated) == []


@pytest.mark.parametrize('original,translated,error', [
    ('Acme invested $5 million', 'Acme 调查了500万美元', 'investment'),
    ('Acme shares declined 5%', 'Acme 股价上涨5%', 'fall'),
    ('Google has not approved the plan', 'Google 已批准该计划', 'negation'),
    ('US government debt rout triggers selling', '美国政府债务暴跌引发抛售', 'bond_price_not_debt_stock'),
    ('Google debuts Gemini 4', 'Google 推出 Gemini 5', 'quantities_or_units'),
])
def test_wrong_translations_still_rejected(original, translated, error):
    assert error in translation_errors(original, translated)


def test_masthead_cannot_supply_investment_event():
    row = {'excerpt': 'Investing.com -- Mastercard payment consortium launched a stablecoin for digital infrastructure.',
           'output_text': 'Mastercard 支付联盟推出用于数字基础设施的稳定币。'}
    assert _rule_for(row).key == 'payment-commercialization'
    for verb in ('investigates', 'investigating'):
        assert _rule_for({'excerpt': f'Microsoft {verb} cloud outages.', 'output_text': '微软调查云服务故障。'}) is None
    assert _rule_for({'excerpt': 'Microsoft invests in a cloud data center.', 'output_text': '微软投资云数据中心。'}).key == 'infrastructure-investment'
    assert _rule_for({'excerpt': 'Microsoft invests in bonds. Cloud service is offline.', 'output_text': '微软投资债券。云服务离线。'}) is None


@pytest.mark.parametrize('title', [
    "What is Howard Buffett's net worth in 2026? The new Berkshire chair's wealth & shares",
    'NEWSLETTER: Inside Anthropic confidential S-1: a Q&A - Reuters',
    'Costco confirms major food court change',
    'Members should be excited to see what is in store.',
])
def test_navigation_and_factless_titles_are_not_evidence(title):
    assert editorial_issue(title)


def test_concrete_body_survives_navigation_headline():
    item = SimpleNamespace(title='Acme confirms major service change', summary='Acme announced a $5 million investment in a new factory.', source='', url='https://example.com', published_at=datetime.now(UTC))
    assert factual_excerpt(item) == item.summary


def test_external_employer_is_not_appointment_beneficiary():
    def item(title):
        return SimpleNamespace(title=title, summary='', source='', url='https://example.com', published_at=datetime.now(UTC))
    assert not company_candidate(item("HealthEquity Appoints Moody’s CFO Noémie Heuland to Board of Directors"), 'MCO')
    assert not company_candidate(item('Acme appoints Microsoft CFO to its board'), 'MSFT')
    assert company_candidate(item('Microsoft appoints Acme CFO to its board'), 'MSFT')


@pytest.mark.parametrize('currency', ['Dollar', 'Euro', 'Yen', 'Sterling'])
def test_currency_performance_is_fx_not_background_inflation(currency):
    assert macro_topic(f'{currency} Wraps Best Month Since March on Inflation Fight') == '外汇市场'


def test_presentation_metadata_spacing_and_historical_replay():
    raw = 'Investing.com -- 泡泡玛特旗舰店落子巴黎 美洲渠道增长20%'
    assert present(raw).text == '泡泡玛特旗舰店落子巴黎 美洲渠道增长20%'
    assert replay_presentation(raw, {'presentation_version': 5}) == 'Investing.com -- 泡泡玛特旗舰店落子巴黎美洲渠道增长20%'
    assert voice_text('Nvidia CEO Jensen Huang 对 AI 实验室表示：“如果担心，那就停下来。”', '黄仁勋') == '对 AI 实验室表示：“如果担心，那就停下来。”'
    assert voice_text('OpenAI IPO 推迟：Sam Altman 称安全优先于华尔街。', '奥特曼') == 'OpenAI IPO 推迟：安全优先于华尔街。'


def test_sentiment_delta_magnitude_and_strategy_boundaries():
    bundle = SentimentBundle([SentimentMetric('CNN Fear & Greed', 30.83, 31.63, None), SentimentMetric('VIX', 16.34, 16.04, None)], datetime.now(UTC))
    assert not _argument_errors('CNN 30.83，VIX 16.34 微升0.30，整体中性。', bundle, '中性')
    assert not _argument_errors('CNN 30.83 较上期31.63回落0.80，VIX 16.34，整体中性。', bundle, '中性')
    assert 'wrong_up_direction' in _argument_errors('CNN 30.83 上涨0.80。', bundle, '中性')
    assert 'unsupported_strategy_claim' in _argument_errors('CNN 30.83，DCA 触发概率上升。', bundle, '中性')


def test_actual_sent_edition_replay():
    import json
    from pathlib import Path

    from src.processors.news_selection import frontier_candidate

    rows = json.loads((Path(__file__).parent / 'fixtures/october1_published_evidence.json').read_text())['sections']
    assert _rule_for(rows['company'][11]).key == 'payment-commercialization'
    for index, ticker in ((0, 'COST'), (3, 'MCO'), (5, 'BRK.B')):
        row = rows['company'][index]
        item = SimpleNamespace(title=row['original_title'], summary=row['original_summary'], source=row['source_name'], url=row['url'])
        assert not company_candidate(item, ticker)
    row = rows['frontier'][0]
    assert not frontier_candidate(SimpleNamespace(title=row['original_title'], summary=row['original_summary'], source=row['source_name'], url=row['url']))
    row = rows['macro'][0]
    assert 'bond_price_not_debt_stock' in translation_errors(row['excerpt'], row['validated_text'])
    assert macro_topic(rows['macro'][3]['original_title']) == '外汇市场'
    for row in rows['company'][9:11]:
        assert ' ' in present(row['validated_text'], source_name=row['source_name']).text
    for row in rows['figures']:
        output = present(row['validated_text'], source_name=row['source_name']).text
        cleaned = voice_text(output, row['presentation_speaker'])
        assert cleaned != output
        assert replay_presentation(row['validated_text'], row) == row['output_text']


def test_each_company_fact_has_punctuation_before_its_citation():
    from src.collectors.company_news import NewsItem
    from src.processors.news_summarizer import _rebuild_safe_summary

    facts = ['腾讯公布营收增长20%', '腾讯宣布回购10亿港元']
    items = [NewsItem(title=t, published_at=datetime.now(UTC), url=f'https://example.com/{i}', source='Source', holding_ticker='0700.HK') for i, t in enumerate(facts)]
    result = _rebuild_safe_summary('\n'.join(f'<strong>腾讯</strong> — {t}[{i+1}]' for i, t in enumerate(facts)), items)
    assert result is not None
    for fact in facts:
        assert fact + '。' in result.summary_html


def test_sentiment_roles_cannot_be_swapped():
    bundle = SentimentBundle([SentimentMetric('CNN Fear & Greed', 30.83, 31.63, None), SentimentMetric('VIX', 16.34, 16.04, None)], datetime.now(UTC))
    assert 'wrong_current_value' in _argument_errors('CNN 当前31.63，VIX 16.34。', bundle, '中性')
    assert 'wrong_prior_value' in _argument_errors('CNN 上期30.83，VIX 16.34。', bundle, '中性')
    assert 'wrong_delta_value' in _argument_errors('CNN 回落31.63，VIX 16.34。', bundle, '中性')


def test_refusal_and_investigation_cannot_disappear():
    assert 'refusal' in translation_errors('Altman declined to testify', 'Altman 作证')
    assert 'investigation' in translation_errors('FTC investigates Acme', 'FTC 投资 Acme')
    assert not translation_errors('FTC launches an investigation into Acme', 'FTC 启动对 Acme 的调查')
    text = '巴菲特称 Nvidia CEO Jensen Huang 对 AI 实验室表示：“停止训练。”'
    assert voice_text(text, '黄仁勋') == text
