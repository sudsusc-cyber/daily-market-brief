"""Regressions from the actual formal edition, with unrelated same-class cases."""
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from src.collectors.company_news import NewsItem
from src.collectors.figures import FigureBundle, FigureMention
from src.collectors.sentiment import SentimentBundle, SentimentMetric
from src.processors.event_semantics import has_event
from src.processors.figure_filter import _has_quote_marker, filter_one
from src.processors.holdings_intro import fallback_intro, write_intro
from src.processors.investment_relevance import long_term_noise_reason
from src.processors.news_selection import company_candidate, factual_excerpt, publishable_excerpt
from src.processors.news_summarizer import _rebuild_safe_summary
from src.processors.sentiment_judge import _argument_errors, one_sentence_summary
from src.processors.translation_guard import translation_errors

NOW = datetime(2026, 10, 3, tzinfo=UTC)


@pytest.mark.parametrize('en,zh', [('service-loss', '通信服务中断'), ('data loss', '数据丢失'), ('power loss', '电力中断')])
def test_nonfinancial_loss_requires_no_invented_financial_loss(en, zh):
    original = f'Acme reports {en} after a bug.'
    translated = f'Acme 报告故障后的{zh}。'
    assert not translation_errors(original, translated)
    assert translation_errors(original, 'Acme 报告亏损。')
    assert translation_errors('Acme reports losses.', translated)


@pytest.mark.parametrize('commodity,zh', [('diesel', '柴油'), ('oil', '石油'), ('petroleum', '石油')])
def test_reserve_release_is_distinct_from_product_or_person_release(commodity, zh):
    original = f'G7 nations to release {commodity} stocks.'
    translated = f'G7 国家将释放{zh}库存。'
    assert not translation_errors(original, translated)
    assert translation_errors(original, translated.replace('将', '已'))
    assert translation_errors(original, 'G7 国家将释放囚犯。')
    assert has_event(original, 'reserve_drawdown')
    assert not has_event(original, 'launch', 'person_release')


def test_financial_loss_and_real_cut_are_still_protected():
    assert translation_errors('Acme reports a loss.', 'Acme 公布盈利。')
    assert translation_errors('Acme cuts dividends.', 'Acme 提高股息。')
    assert not translation_errors('Acme cut another quarterly check.', 'Acme 又支付了季度支票。')
    assert translation_errors('Acme cut another quarterly check.', 'Acme 削减了季度支票。')


def test_contextual_macro_excerpt_retains_bond_background():
    from src.processors.macro_events import edition_events
    summary = 'Wall Street has spent weeks trying to make peace with the great bond selloff. Friday offered some short-lived relief — along with a warning about the damage from stubbornly high yields across investment strategies of all stripes.'
    item = NewsItem('Wall Street Tries to Live With 5% Yields as Market Cracks Grow', NOW, 'https://example.com/bonds', 'Source', summary)
    excerpt = factual_excerpt(item)
    assert excerpt == summary
    assert not publishable_excerpt(item, summary.split('. ', 1)[1])
    assert edition_events([excerpt])[0].family == 'bonds'


def test_generic_roundup_needs_holding_specific_operating_fact():
    item = NewsItem('Industry Voices: executives defend spending this week', NOW, 'https://example.com/voices', 'Source', 'Industry leaders debated the future.', 'MSFT')
    assert not company_candidate(item, 'MSFT')
    item.summary = 'Microsoft signed a cloud contract with a hospital.'
    assert company_candidate(item, 'MSFT')
    assert factual_excerpt(item) == item.summary


def test_personality_dispute_has_to_contain_operating_information():
    assert long_term_noise_reason('Executives privately confront rival over warnings')
    assert not long_term_noise_reason('Executives privately confront rival over warnings', 'Acme signed a supply contract.')


def test_joint_fact_has_one_row_and_preserves_both_company_labels():
    item = NewsItem('Microsoft and Google signed a cloud agreement.', NOW, 'https://example.com/joint', 'Source', holding_ticker='MSFT', related_holding_tickers=('MSFT', 'GOOG'))
    item.source_excerpt = item.title
    item.translated_excerpt = 'Microsoft 和 Google 签署了云协议。'
    raw = '<strong>微软</strong> — Microsoft 和 Google 签署了云协议。[1]\n<strong>谷歌</strong> — Microsoft 和 Google 签署了云协议。[1]'
    out = _rebuild_safe_summary(raw, [item])
    assert out and out.summary_html.count('签署了云协议') == 1
    assert '微软 / 谷歌' in out.summary_html and len(out.footnotes) == 1
    assert out.evidence[0]['presentation_companies'] == ['微软', '谷歌']


def test_sentiment_valid_computed_roles_and_wrong_roles():
    bundle = SentimentBundle(fetched_at=NOW,metrics=[SentimentMetric('CNN Fear & Greed',31.1714285714286,28.0857142857143,None), SentimentMetric('VIX',15.31,16.39,None), SentimentMetric('DXY',101.93,102.10,None), SentimentMetric('Shiller PE',41.38,None,None), SentimentMetric('高收益债利差',3.24,3.12,None,unit='%')])
    argument = 'CNN 处于 31.17 的恐惧区间、权重 0.45，VIX 为 15.31、权重 0.40，两者一低一高相互抵消，加权后落在 53.0 的中性档位。'
    assert not _argument_errors(argument,bundle,'中性')
    assert _argument_errors(argument.replace('0.45','0.95'),bundle,'中性')
    assert _argument_errors(argument.replace('53.0','83.0'),bundle,'中性')


@pytest.mark.parametrize('text', ['指标变化不参与评分。', '市场情绪中性，指标变化不参与评分。', '市场情绪中性。指标变化不参与评分。'])
def test_forbidden_implementation_phrase_cannot_reappear_from_cache(text):
    assert '指标变化不参与评分' not in one_sentence_summary(text)


def test_no_signal_clause_in_generated_or_fallback_intro():
    prose = '价格从不负责解释自己，它只是把选择摆在面前。真正的功课在别处：辨认那些在无人注视时依然一寸寸积累的价值，然后让事先写下的规则，替临场的情绪做决定。'
    for state in ['NONE','DCA','LUMP_SUM']:
        signals = [SimpleNamespace(signal=state,error=None)]
        client = SimpleNamespace(chat=lambda *a,**k: SimpleNamespace(text=json.dumps({'text':prose})))
        assert write_intro(signals,client=client) == prose
        assert '买入区间' not in fallback_intro(signals,NOW)


def test_voices_prefilter_does_not_let_unqualified_rows_consume_limit():
    items = [FigureMention('Company earnings report', '', NOW, 'https://example.com/no', 'Source') for _ in range(5)]
    items += [FigureMention('Alex North expects demand to double.', '', NOW, 'https://example.com/yes', 'Source')]
    seen = []
    def chat(payload,**kwargs):
        seen.append(payload)
        return SimpleNamespace(text='▦ 1: no | score=2 | 信息不足',error=None)
    result = filter_one(FigureBundle('Alex North','query',items=items),client=SimpleNamespace(chat=chat))
    assert seen and 'Alex North expects' in seen[0]
    audit = next(a for a in result.verification_audit if a['phase']=='rule_selection')
    assert sum(d['reason']=='selected' for d in audit['decisions']) == 1
    assert sum(d['reason']=='no_attributed_speech_marker' for d in audit['decisions']) == 5


def test_company_announcement_stays_distinct_from_named_person_statement():
    for title,expected in [('Microsoft announced a product',False),('Alex North announces an investment plan',True)]:
        assert _has_quote_marker(FigureMention(title,'',NOW,'https://example.com/1','Source')) is expected


def test_presentation_name_changes_are_versioned():
    from src.processors.news_presentation import present, replay_presentation
    assert '黄仁勋' in present('Jensen Huang 表示将投资。').text
    assert 'Jensen Huang' in replay_presentation('Jensen Huang 表示将投资。', {'presentation_version':11})


def test_all_five_real_macro_false_rejections_recover_with_original_facts():
    from pathlib import Path
    rows = json.loads((Path(__file__).parent/'fixtures/october3_formal_translation_rejections.json').read_text())
    assert len(rows) == 5
    for row in rows:
        assert not translation_errors(row['excerpt'], row['translation']), row['title']
    low = next(r for r in rows if 'little chance' in r['excerpt'])
    assert translation_errors(low['excerpt'], low['translation'].replace('可能性很小', '可能性很大'))
    numbered = next(r for r in rows if '29,000' in r['excerpt'])
    assert translation_errors(numbered['excerpt'], numbered['translation'].replace('29,000', '290,000'))


def test_contextual_bond_paragraph_merges_with_other_bond_news():
    from src.processors.macro_filter import _rebuild_safe_html
    summary = 'Wall Street has spent weeks trying to make peace with the great bond selloff. Friday offered some short-lived relief — along with a warning about the damage from stubbornly high yields across investment strategies of all stripes.'
    item = NewsItem('Wall Street Tries to Live With 5% Yields as Market Cracks Grow', NOW, 'https://example.com/context', 'Source', summary)
    item.source_excerpt = factual_excerpt(item)
    item.translated_excerpt = '华尔街数周以来一直试图适应债券大幅抛售。周五出现短暂缓解，但持续高企的收益率也在损害各类投资策略。'
    other = NewsItem('欧洲股市受债券收益率高企影响承压。', NOW, 'https://example.com/europe', 'Source')
    # Closed excerpt binding and facts, not a free-form synthesized explanation.
    assert not translation_errors(item.source_excerpt, item.translated_excerpt)
    evidence=[]
    html,notes = _rebuild_safe_html('<p>市场。[1][2]</p>',[item,other],evidence)
    assert len(notes)==2 and html.count('data-macro-heading=') == 1
    assert '债券大幅抛售' in html and len(evidence)==2


def test_person_name_localization_cannot_make_english_prose_publishable():
    from src.processors.source_grounding import grounded_text
    item = NewsItem('Jensen Huang says AI is useful.', NOW, 'https://example.com/untranslated','Source')
    assert grounded_text(item.title,[item]) == ('', [])
