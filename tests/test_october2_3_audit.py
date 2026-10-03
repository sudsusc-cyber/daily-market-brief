"""Production October 2/3 evidence and unrelated same-class counterexamples."""
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.collectors.company_news import NewsItem
from src.processors.macro_events import edition_events
from src.processors.news_presentation import company_body, present, replay_presentation
from src.processors.news_selection import factual_excerpt, publishable_excerpt
from src.processors.thesis.renderer import _event_subject
from src.processors.translation_guard import translation_errors

ROWS = json.loads((Path(__file__).parent / 'fixtures/october2_3_mail_sources.json').read_text())


def item(row):
    return NewsItem(row['original_title'], datetime.fromisoformat(row['published_at']), row['url'], row['source_name'], row['original_summary'], row.get('presentation_company'))


@pytest.mark.parametrize('speaker,company', [('Sam Altman', 'OpenAI'), ('Satya Nadella', 'Microsoft')])
def test_attribution_movement_is_not_actor_reversal(speaker, company):
    original = f'{company} Will Keep Driving Down AI Prices, {speaker} Says'
    translated = f'{speaker} 称 {company} 将继续压低 AI 价格'
    assert not translation_errors(original, translated)
    assert 'modality' in translation_errors(original, translated.replace('将继续', '已经'))
    assert translation_errors('Microsoft will acquire OpenAI, Sam Altman says', 'Sam Altman 称 OpenAI 将收购 Microsoft')


def test_lending_headline_future_and_ceiling_are_preserved():
    original = 'EXCLUSIVE: Broadcom to lend Anthropic up to $42 billion to lease its chips, filing says'
    good = '独家：Broadcom 将向 Anthropic 提供最高 420 亿美元贷款，用于租赁其芯片，文件显示'
    assert not translation_errors(original, good)
    assert 'quantities_or_units' in translation_errors(original, good.replace('最高 ', ''))
    assert 'modality' in translation_errors(original, good.replace('将向', '已向'))


def test_real_wire_fragment_uses_intact_title_not_missing_subject():
    row = next(r for r in ROWS['2026-10-02']['company'] if 'askpolly' in r['original_title'])
    source = item(row)
    assert factual_excerpt(source) == source.title
    assert not publishable_excerpt(source, ', the first company capable of research, today announced its Microsoft integration.')


def test_promotional_company_description_prefers_compact_source_fact():
    row = next(r for r in ROWS['2026-10-02']['company'] if 'Moody’s Analytics and Allvue' in r['original_title'])
    assert factual_excerpt(item(row)) == row['original_title']


@pytest.mark.parametrize('text,ticker', [
    ('Alphabet Inc 旗下的 Google 发布了新模型。', 'GOOG'),
    ('腾讯支持的企业申请上市。', '0700.HK'),
    ('Microsoft 和其他科技公司成立了联盟。', 'MSFT'),
    ('Apple 投资的企业公布营收。', 'AAPL'),
])
def test_issuer_modifiers_and_joint_subjects_remain_intact(text, ticker):
    assert company_body(text, ticker) == text


def test_full_legal_suffix_removed_only_with_direct_predicate():
    assert company_body('Alphabet Inc 发布了新模型。', 'GOOG') == '发布了新模型。'
    assert company_body('Microsoft Corporation 发布了一款新产品。', 'MSFT') == '发布了一款新产品。'
    assert 'TW:2330' not in present('TSMC (TW:2330) 正在评估投资。').text
    assert '(AI)' in present('企业宣布人工智能 (AI) 产品。').text


def test_historical_presentation_remains_replayable():
    for sections in ROWS.values():
        for row in sections['company']:
            assert replay_presentation(row['validated_text'], row) == row['output_text']


def test_macro_jobs_release_and_reactions_form_one_topic():
    rows = ROWS['2026-10-03']['macro']
    events = edition_events([r['excerpt'] for r in rows])
    assert [e.topic for e in events] == ['就业市场'] * 3
    unrelated = edition_events(['US economy added 29000 jobs in September.', 'Treasury yields rise after oil prices surge.', 'Japan bond yields rise after weak jobs data.'])
    assert unrelated[1].topic == '美债市场'
    assert unrelated[2].topic != '就业市场'


@pytest.mark.parametrize('month', ['April', 'October', 'May'])
def test_dates_never_become_product_identity(month):
    row = {'excerpt': 'Apple renewed a patent agreement.', 'original_title': f'Apple license effective {month} 2027', 'presentation_company': 'AAPL'}
    assert _event_subject(row) == '苹果'
    row['original_title'] = 'Apple launches Model 7 Pro'
    assert _event_subject(row) == '苹果 Model 7 Pro'


def test_price_only_new_high_does_not_fill_company_news():
    from src.processors.news_selection import company_candidate
    source = SimpleNamespace(title='Nvidia hits all-time high, market cap reaches $5.7 trillion', summary='', source='Yahoo')
    assert not company_candidate(source, 'NVDA')


def test_broker_recommendation_is_not_rescued_by_vague_momentum():
    from src.processors.news_selection import company_candidate
    row = next(r for r in ROWS['2026-10-02']['company'] if 'Tactical Ideas' in r['original_title'])
    assert not company_candidate(item(row), 'MSFT')


def test_operating_fact_survives_editorial_tail():
    row = next(r for r in ROWS['2026-10-02']['company'] if 'Real Prize' in r['original_title'])
    assert factual_excerpt(item(row)) == 'Nvidia Authorizes a Record $150 Billion in Stock Buybacks.'


def test_anaphoric_partnership_keeps_issuer_and_real_partner():
    row = next(r for r in ROWS['2026-10-03']['company'] if 'Smart Home' in r['original_title'])
    excerpt = factual_excerpt(item(row))
    assert 'partnering with LG Electronics' in excerpt
    assert excerpt.startswith('Microsoft')
    assert excerpt in row['original_summary']


def test_unknown_speaker_cannot_change_during_attribution_reordering():
    assert translation_errors('Microsoft will launch a product, Alex North says', 'Robin South 称 Microsoft 将发布一款产品')


def test_actual_selected_voice_publishes_after_translation_recovery():
    from src.collectors.figures import FigureMention
    from src.processors.source_grounding import grounded_text
    source = FigureMention(title='OpenAI Will Keep Driving Down AI Prices, Sam Altman Says - Moomoo',
                           url='https://example.com/voice', source='Moomoo',
                           published_at=datetime(2026, 10, 1), snippet='')
    source.source_excerpt = source.title
    source.translated_excerpt = 'Sam Altman 称 OpenAI 将继续压低 AI 价格 - Moomoo'
    text, mappings = grounded_text(source.translated_excerpt, [source])
    assert text == 'Sam Altman 称 OpenAI 将继续压低 AI 价格'
    assert mappings[0]['excerpt'] == source.title


def test_attribution_does_not_equate_distinct_people_with_same_surname():
    assert translation_errors('OpenAI will launch a model, Sam Altman says', 'Alex Altman 称 OpenAI 将发布一个模型')


def test_macro_reactions_do_not_borrow_another_country_or_period():
    events = edition_events(['US economy added 29000 jobs in September.',
                             'Canadian dollar falls after weak jobs data.',
                             'Treasury yields rise after revised August jobs data.'])
    assert [e.topic for e in events] == ['就业市场', '外汇市场', '美债市场']


def test_analyst_headline_can_recover_real_operating_announcement():
    from src.processors.news_selection import company_candidate
    source = SimpleNamespace(title='Analyst sees 30% upside potential for Microsoft',
                             summary='Microsoft announced revenue growth of 12%.', source='Source')
    assert company_candidate(source, 'MSFT')


def replay_company(edition):
    from src.processors.news_selection import company_candidate
    from src.processors.news_summarizer import _rebuild_safe_summary
    from src.processors.presentation_vocabulary import COMPANY_DISPLAY_NAMES
    translations = {
        'askpolly Now Integrated With Microsoft Copilot Cowork to Connect Social Insights With Sales Data':
            'askpolly 现已与 Microsoft Copilot Cowork 集成，以连接社交洞察与销售数据',
        'Moody’s Analytics and Allvue Launch Credit Risk Model to Identify Early Signs of Borrower Stress in Private Credit':
            'Moody’s Analytics 与 Allvue 推出信用风险模型，以识别私募信贷中借款人压力的早期迹象',
        'Nvidia Authorizes a Record $150 Billion in Stock Buybacks. The Real Prize Is Where the Rest of Its Cash Is Going.':
            'Nvidia 批准创纪录的 1500 亿美元股票回购。',
        'Microsoft Entered the Smart Home Through Your Washing Machine, Not Your Phone':
            'Microsoft 终于找到了进入消费者智能家居的方式，但不是通过智能手机或专用智能音箱。相反，这家科技巨头正绕过过去十年定义该行业的传统入口，与 LG Electronics 合作将智能直接嵌入家用电器。',
    }
    items, raw = [], []
    for row in ROWS[edition]['company']:
        source = item(row)
        if not company_candidate(source, source.holding_ticker):
            continue
        source.source_excerpt = factual_excerpt(source) if source.title in translations else row['excerpt']
        source.translated_excerpt = translations.get(source.title, row['validated_text'])
        assert not translation_errors(source.source_excerpt, source.translated_excerpt)
        items.append(source)
        raw.append(f'<strong>{COMPANY_DISPLAY_NAMES[source.holding_ticker]}</strong> — {source.translated_excerpt}[{len(items)}]')
    return _rebuild_safe_summary('\n'.join(raw), items)


@pytest.mark.parametrize('edition', ['2026-10-02', '2026-10-03'])
def test_production_company_replay(edition):
    summary = replay_company(edition)
    assert summary and not summary.content_rejections
    assert len(summary.footnotes) >= 9
    assert 'ACCESS Newswire' not in summary.summary_html
    assert 'Inc 旗下' not in summary.summary_html
    assert '真正的看点' not in summary.summary_html
    for row in summary.evidence:
        assert row['output_text'] == replay_presentation(row['validated_text'], row)


def test_real_macro_upper_bound_survives_intervening_action():
    row = ROWS['2026-10-02']['macro'][0]
    assert not translation_errors(row['excerpt'], row['validated_text'])
    assert 'quantities_or_units' in translation_errors(row['excerpt'], row['validated_text'].replace('最多', ''))
