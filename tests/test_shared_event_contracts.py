"""Event-family regressions, including synonymous wording and false friends."""
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.processors.announcement_context import action_context
from src.processors.news_presentation import present, replay_presentation
from src.processors.news_selection import company_candidate, factual_excerpt
from src.processors.source_grounding import grounded_text
from src.processors.thesis.renderer import _rule_for
from src.processors.translation_guard import translation_errors

NOW = datetime(2026, 9, 30, tzinfo=UTC)


def article(title, summary='', ticker=None):
    return SimpleNamespace(title=title, summary=summary, holding_ticker=ticker,
                           published_at=NOW, url='https://example.com/news', source='Source')


@pytest.mark.parametrize('verb', ['investigates', 'probes', 'probed', 'is probing'])
@pytest.mark.parametrize('issuer', ['OpenAI', 'Acme', 'Northstar'])
def test_inquiry_inflections_are_equivalent(verb, issuer):
    assert not translation_errors(f'US regulator {verb} {issuer} over safety', f'美国监管机构就安全调查 {issuer}')
    assert translation_errors(f'US regulator {verb} {issuer} over safety', f'美国监管机构就安全批准 {issuer}')


@pytest.mark.parametrize('english,chinese', [
    ('Investors drive government bond yields higher', '投资者推高政府债券收益率'),
    ('Investor relations website', '投资者关系网站'),
    ('Investment bank reports revenue', '投资银行公布营收'),
    ('An investment firm buys shares', '一家投资公司买入股票'),
])
def test_roles_are_not_investment_events(english, chinese):
    assert not translation_errors(english, chinese)
    assert 'investment' in translation_errors(english, chinese + '并投资新厂')


@pytest.mark.parametrize('verb', ['opens', 'launches', 'starts'])
def test_inquiry_object_is_not_product_launch(verb):
    assert not translation_errors(f'FTC {verb} a consumer-protection probe of Acme', 'FTC 对 Acme 启动消费者保护调查')
    assert translation_errors(f'FTC {verb} a consumer-protection probe of Acme', 'FTC 对 Acme 启动消费者保护平台')


@pytest.mark.parametrize('verb', ['debuts', 'unveils', 'launches', 'introduces', 'releases'])
@pytest.mark.parametrize('issuer', ['Google', 'Microsoft', 'Apple'])
def test_launch_synonyms_share_selection_translation_and_watchpoint(verb, issuer):
    original = f'{issuer} {verb} its new AI model'
    translated = f'{issuer} 推出其新 AI 模型'
    assert not translation_errors(original, translated)
    assert factual_excerpt(article(original)) == original
    assert _rule_for(dict(excerpt=original, output_text=translated)).key == 'product-commercialization'


@pytest.mark.parametrize('verb', ['authorizes', 'approves', 'greenlights'])
def test_capital_return_synonyms_match_same_watchpoint(verb):
    source = f'Microsoft {verb} a $10 billion buyback'
    output = 'Microsoft 批准 100 亿美元回购'
    assert not translation_errors(source, output)
    assert _rule_for(dict(excerpt=source, output_text=output)).key == 'capital-allocation'
    assert translation_errors(source, 'Microsoft 已执行 100 亿美元回购')


@pytest.mark.parametrize('broker', ['Piper Sandler', 'Northstar Securities', 'Acme Research'])
@pytest.mark.parametrize('issuer,ticker', [('Microsoft','MSFT'), ('NVIDIA','NVDA'), ('Apple','AAPL')])
def test_ratings_are_not_operating_news(broker, issuer, ticker):
    title = f'{broker} Maintains Overweight on {issuer}, Raises Price Target to $610'
    item = article(title, ticker=ticker)
    assert not company_candidate(item, ticker)
    # A genuine business event remains publishable even in an analyst article.
    item.summary = f'{issuer} introduced a new AI platform.'
    assert factual_excerpt(item) == item.summary
    assert company_candidate(item, ticker)


@pytest.mark.parametrize('issuer,ticker', [('American Express','AXP'), ('Microsoft','MSFT')])
def test_product_noun_fragment_uses_full_body_event(issuer, ticker):
    item = article(f"{issuer}'s New AI Agent for Expenses", ticker=ticker)
    assert not factual_excerpt(item)
    item.summary = f'{issuer} reveals a new AI powered corporate platform for expense reports.'
    assert factual_excerpt(item) == item.summary


@pytest.mark.parametrize('bank', ['Bank of Japan', 'Bank of England', 'Acme Central Bank'])
def test_document_type_is_preserved_for_any_bank(bank):
    original = f'{bank} releases its summary of opinions'
    assert not translation_errors(original, f'{bank} 发布意见摘要')
    assert 'document_type' in translation_errors(original, f'{bank} 发布会议纪要')
    assert 'document_type' in translation_errors(f'{bank} releases its meeting minutes', f'{bank} 发布意见摘要')


def test_ambiguous_document_headline_uses_typed_body():
    item = article('Yen Weakens as BOJ Summary Damps Rate Hike Bets',
                   'The yen weakened after a summary of opinions from the Bank of Japan disappointed investors.')
    assert factual_excerpt(item) == item.summary


@pytest.mark.parametrize('issuer,ticker', [('NVIDIA','NVDA'), ('Apple','AAPL'), ('Microsoft','MSFT')])
def test_wrap_timestamp_never_supplies_announcement_date(issuer, ticker):
    title = f'Stock Market Today, Sept. 30: {issuer} Authorizes $150B Buyback, Lifting Total to $235B'
    item = article(title, 'Today, Sept. 30, 2026, management reinforced confidence with a repurchase program.', ticker)
    assert factual_excerpt(item) == ''
    for day, kept in [('28', False), ('30', True)]:
        body = f'On September {day}, 2026, {issuer} approved an additional $150 billion buyback, bringing remaining authorization to $235 billion.'
        item.summary = body
        assert bool(action_context(item)) is kept
    assert factual_excerpt(item) == item.summary
    item.source_excerpt = item.summary
    item.translated_excerpt = f'2026 年 9 月 30 日，{issuer} 批准新增 1500 亿美元回购，使剩余授权额度达到 2350 亿美元。'
    text, mapping = grounded_text(item.translated_excerpt, [item])
    assert text and mapping[0]['event_date'] == '2026-09-30'
    assert mapping[0]['event_date_basis'] == 'dated_source_excerpt'


def test_amount_role_cannot_be_dropped_or_swapped():
    source = 'Microsoft approved an additional $150 billion buyback, bringing remaining authorization to $235 billion.'
    good = 'Microsoft 批准新增 1500 亿美元回购，使剩余授权额度达到 2350 亿美元。'
    assert not translation_errors(source, good)
    assert 'capital_amount_scope' in translation_errors(source, good.replace('剩余授权额度','总额'))
    assert 'capital_amount_scope' in translation_errors(source, good.replace('1500','TEMP').replace('2350','1500').replace('TEMP','2350'))


def test_localisation_is_versioned_and_keeps_product_identifiers():
    text = 'Eurozone、BOJ、Arizona、Texas；Gemini 4 Argon、Watch FIT 5'
    assert present(text).text == '欧元区、日本央行、亚利桑那州、得克萨斯州；Gemini 4 Argon、Watch FIT 5'
    assert replay_presentation(text, {'presentation_version':8}) == text


def test_received_sources_and_legacy_presentation():
    rows = json.loads((Path(__file__).parent/'fixtures/october1_resend_sources.json').read_text())
    for section in rows.values():
        for row in section:
            assert replay_presentation(row['validated_text'], row) == row['output_text']
    microsoft, nvidia, _, google, amex = rows['company'][:5]
    def item(row, ticker):
        return article(row['original_title'], row['original_summary'], ticker)
    assert not company_candidate(item(microsoft, 'MSFT'), 'MSFT')
    assert not factual_excerpt(item(nvidia, 'NVDA'))
    assert _rule_for(google).key == 'product-commercialization'
    assert factual_excerpt(item(amex, 'AXP')) == amex['original_summary']
    yen = rows['macro'][1]
    assert 'summary_not_minutes' in translation_errors(yen['excerpt'], yen['validated_text'])


def test_credit_rating_is_not_broker_stock_advice():
    from src.processors.news_selection import analyst_opinion

    assert not analyst_opinion('评级机构下调该公司的信用评级。')
    assert not analyst_opinion('The regulator withdrew a bank credit rating.')
    assert analyst_opinion('券商维持公司的增持评级。')


def test_financing_participation_and_institution_roles_do_not_conflict():
    source = 'The Series B was led by an investment firm, with participation from NVIDIA.'
    target = 'B 轮融资由一家投资公司领投，NVIDIA 参与投资。'
    assert not translation_errors(source, target)
    assert 'investment' in translation_errors('A conference included participation from NVIDIA.', '一场会议包括 NVIDIA 的投资。')


@pytest.mark.parametrize('title', ["Amex's AI Agent for Expenses", "Amex's New AI Agent for Expenses", 'American Express expense platform'])
def test_product_fragments_need_an_event_even_without_new_keyword(title):
    assert not factual_excerpt(article(title, ticker='AXP'))


def test_known_geography_can_be_localized_but_not_swapped():
    assert not translation_errors('A plant in Texas', '位于得克萨斯州的一座工厂')
    assert translation_errors('A plant in Texas', '位于亚利桑那州的一座工厂')
    assert not translation_errors('Bank of Japan releases its summary of opinions', '日本央行发布意见摘要')


@pytest.mark.parametrize("text", [
    "A researcher says AI agents remain unreliable.",
    "一位研究员指出模型仍有技术风险。",
    "The CEO warned that the platform has operational risks.",
])
def test_complete_attributed_opinion_is_not_a_product_noun_fragment(text):
    from src.processors.news_selection import noun_fragment
    assert not noun_fragment(text)
