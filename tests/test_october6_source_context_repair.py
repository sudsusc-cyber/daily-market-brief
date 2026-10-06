import json
import time
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.collectors.company_news import NewsItem
from src.collectors.macro_news import MacroNewsItem
from src.processors.holdings_intro import _signal_errors, _verified_fallback_signal, write_intro
from src.processors.news_selection import factual_excerpt
from src.processors.source_grounding import checked_excerpt, source_sentences
from src.processors.thesis.renderer import _rule_for
from src.processors.translation_guard import translation_errors
from src.processors.translator import _source_bound_transport
from src.valuation.morningstar import (
    SECURITIES,
    MorningstarFairValue,
    MorningstarPublicProvider,
    _Candidate,
)

NOW = datetime(2026, 10, 6, tzinfo=UTC)
PROSE = ('看一家企业，终究要回到它是否在无人喝彩时仍做对的事。价格会喧哗，价值却安静生长，'
         '耐心不是等待，而是理解之后的从容；【持仓近况】；把判断交给时间，把动作交给规则。')
SIGNALS = [SimpleNamespace(error=None, holding=SimpleNamespace(name='甲企业'), signal='LUMP_SUM'),
           SimpleNamespace(error=None, holding=SimpleNamespace(name='乙企业'), signal='DCA')]


def test_accepted_reflection_survives_two_failed_signal_repairs():
    client = Mock()
    client.chat.return_value.text = json.dumps({'text': PROSE, 'signal_text': '甲企业首次进入大额买入区间。'}, ensure_ascii=False)
    result = write_intro(SIGNALS, client=client)
    assert result and result.startswith(PROSE.split('【持仓近况】')[0])
    assert result.endswith(PROSE.split('【持仓近况】')[1])
    assert '首次' not in result and client.chat.call_count == 2


def test_spatial_state_is_compositional_and_does_not_authorize_new_trigger():
    good = '甲企业落在大额买入的区间，乙企业现处定投的余地之内。'
    assert not _signal_errors(good, SIGNALS, [])
    assert _signal_errors(good.replace('落在', '首次进入'), SIGNALS, [])
    assert _signal_errors(good.replace('之内', '之外'), SIGNALS, [])


def test_bounded_fallback_avoids_last_seven_actual_signal_wordings():
    recent = []
    for _ in range(8):
        text = _verified_fallback_signal(SIGNALS, recent[-7:], 2)
        assert not _signal_errors(text, SIGNALS, recent[-7:])
        recent.append(text)
    assert len(set(recent)) == 8


@pytest.mark.parametrize('instrument', ['Euro', 'Yen', 'Sterling'])
def test_currency_standfirst_keeps_instrument_and_record_window(instrument):
    item = MacroNewsItem(f'{instrument} slides to 17-month low against dollar', NOW, 'https://example.com/fx', 'News',
                         summary='High energy prices add to pressure on the single currency')
    assert factual_excerpt(item) == item.title + '\n' + item.summary
    assert source_sentences(item) == [factual_excerpt(item)]


def test_original_numeric_headline_survives_vague_standfirst():
    item = NewsItem("Berkshire Hathaway Says It's Now America's 4th-Largest Homebuilder. It Also Owns More Than 10% of Lennar.", NOW,
                    'https://example.com/berkshire', 'News', summary="Berkshire is investing in housing in a big way, despite the industry's many woes.")
    assert factual_excerpt(item) == item.title


@pytest.mark.parametrize('first, second', [
    ('Elon Musk confirmed Terafab talks with TSMC.', 'Intel has been its only named chip partner.'),
    ('Acme discussed a deal with Northstar.', 'Another firm is its only named partner.'),
])
def test_unresolved_external_partner_pronoun_does_not_replace_intact_event(first, second):
    item = NewsItem(first + ' ' + second, NOW, 'https://example.com/event', 'News')
    assert factual_excerpt(item) == first
    assert item.title not in source_sentences(item)
    item.source_excerpt = item.title
    item.translated_excerpt = 'Acme 与 Northstar 讨论一项协议。Another firm 是其唯一点名的合作伙伴。'
    assert checked_excerpt(item) == ('', '')


@pytest.mark.parametrize('source, translated', [
    ("Jensen Huang Says Humanoid Robots Are 'Very Very Close' To Industrial Reality As Nvidia, Hyundai Deepen Alliance", '黄仁勋称人形机器人距离工业现实“非常非常近”，Nvidia、Hyundai 深化联盟'),
    ('Sam Altman says OpenAI has not approved the plan.', '奥特曼称 OpenAI 尚未批准该计划。'),
    ('Microsoft will acquire Google, Jensen Huang says.', 'Microsoft 将收购 Google，黄仁勋称。'),
])
def test_bilingual_attribution_keeps_one_identity(source, translated):
    assert not translation_errors(source, translated)
    assert translation_errors(source, translated.replace('黄仁勋', '奥特曼')) if '黄仁勋' in translated else translation_errors(source, translated.replace('奥特曼', '黄仁勋'))


def test_bilingual_attribution_does_not_hide_actor_object_swap():
    assert translation_errors('Jensen Huang says Microsoft will acquire Google.', '黄仁勋称 Google 将收购 Microsoft。')


def test_only_added_outer_transport_quotes_are_removed():
    source = 'China closed 670 banks last year.'
    translated = '“中国去年关闭了670家银行。”'
    assert _source_bound_transport(translated, source) == translated[1:-1]
    assert _source_bound_transport(translated, '"China closed 670 banks last year."') == translated
    assert _source_bound_transport('他称“尚未批准”。', source) == '他称“尚未批准”。'
    assert translation_errors(source, _source_bound_transport('“中国今年关闭了760家银行。”', source))


@pytest.mark.parametrize('original, translated, expected', [
    ('Google is close to an agreement to buy nuclear energy for $1 billion.', 'Google 即将达成10亿美元核能购买协议。', 'resource-procurement'),
    ('Mastercard signs a reseller agreement to create a new distribution channel.', 'Mastercard 签署经销商协议，开辟新分销渠道。', 'commercial-distribution'),
    ('TSMC confirms talks to expand chip manufacturing.', 'TSMC 确认扩大芯片制造的谈判。', 'strategic-negotiation'),
])
def test_long_term_watchpoints_match_original_and_visible_event(original, translated, expected):
    row = {'excerpt': original, 'output_text': translated}
    assert _rule_for(row).key == expected
    assert _rule_for(dict(row, output_text='企业的长期前景值得关注。')) is None


def test_macro_sector_watchpoint_never_infers_a_holding():
    row = {'excerpt': 'China shuts banks to consolidate the financial system.',
           'output_text': '中国关闭银行以整合金融体系。', 'macro_event': {'geography': ['china']}}
    assert _rule_for(row).key == 'financial-consolidation'
    assert _rule_for(dict(row, macro_event={})) is None


def value(ticker):
    security = SECURITIES[ticker]
    return MorningstarFairValue(ticker, security.provider_code, 500, security.currency,
        'published-research', '2026-10-05', NOW.isoformat(), 'Morningstar', 'https://www.morningstar.com/stocks/test')


def test_one_slow_issuer_cannot_starve_later_issuer(monkeypatch):
    provider = MorningstarPublicProvider(session=Mock(), secondary_provider=None, stage_seconds=0.2)
    calls = []
    def discover(security):
        calls.append(security.ticker)
        if security.ticker == 'MSFT':
            time.sleep(0.4)
        return [_Candidate('https://www.morningstar.com/stocks/test', NOW)]
    monkeypatch.setattr(provider, '_discover', discover)
    monkeypatch.setattr(provider, '_read', lambda _, security: value(security.ticker))
    values, failures = provider.fetch_all({k: SECURITIES[k] for k in ['MSFT', 'TSM']}, checked_at=NOW)
    assert calls == ['MSFT', 'TSM']
    assert set(values) == {'TSM'} and set(failures) == {'MSFT'}
    assert values['TSM'].fair_value_updated_at == '2026-10-05'


def test_independent_source_does_not_rebind_current_issuer(monkeypatch):
    secondary = Mock()
    secondary.discovery_diagnostics = {}
    secondary.fetch_all.return_value = ({'TSM': value('TSM')}, {})
    provider = MorningstarPublicProvider(session=Mock(), secondary_provider=secondary)
    monkeypatch.setattr(provider, '_discover', lambda _: [])
    values, failures = provider.fetch_all({'MSFT': SECURITIES['MSFT']}, checked_at=NOW)
    assert values == {} and set(failures) == {'MSFT'}


@pytest.mark.parametrize('seconds', [0, -1, 331, float('nan'), float('inf')])
def test_issuer_fairness_cannot_expand_existing_stage_limit(seconds):
    with pytest.raises(ValueError):
        MorningstarPublicProvider(session=Mock(), secondary_provider=None, stage_seconds=seconds)


def test_timeout_before_reconciliation_cannot_publish_partial_observation(monkeypatch):
    import src.valuation.morningstar as module
    secondary = Mock()
    secondary.discovery_diagnostics = {}
    secondary.fetch_all.side_effect = lambda securities, **_: ({ticker: value(ticker) for ticker in securities}, {})
    provider = MorningstarPublicProvider(session=Mock(), secondary_provider=secondary, stage_seconds=0.2)
    monkeypatch.setattr(provider, '_discover', lambda _: [_Candidate('https://www.morningstar.com/stocks/test', NOW)])
    monkeypatch.setattr(provider, '_read', lambda _, security: value(security.ticker))
    reconcile = module._reconcile_independent_sources
    def interrupted(primary, distributed):
        if primary.ticker == 'MSFT':
            time.sleep(0.4)
        return reconcile(primary, distributed)
    monkeypatch.setattr(module, '_reconcile_independent_sources', interrupted)
    values, failures = provider.fetch_all({k: SECURITIES[k] for k in ['MSFT', 'TSM']}, checked_at=NOW)
    assert set(values) == set(provider._inflight_values) == {'TSM'}
    assert set(failures) == {'MSFT'}


def test_slow_distributed_source_reserves_time_for_official_read(monkeypatch):
    secondary = Mock()
    secondary.discovery_diagnostics = {}
    secondary.fetch_all.side_effect = lambda *_args, **_kwargs: time.sleep(0.4)
    provider = MorningstarPublicProvider(session=Mock(), secondary_provider=secondary, stage_seconds=0.15)
    monkeypatch.setattr(provider, '_discover', lambda _: [_Candidate('https://www.morningstar.com/stocks/test', NOW)])
    read = Mock(side_effect=lambda _, security: value(security.ticker))
    monkeypatch.setattr(provider, '_read', read)
    values, failures = provider.fetch_all({'MSFT': SECURITIES['MSFT']}, checked_at=NOW)
    assert set(values) == {'MSFT'} and failures == {} and read.call_count == 2


def test_complete_name_cannot_be_normalized_through_someone_elses_surname():
    assert translation_errors('Sam Altman says OpenAI has not approved the plan.', 'Alex Altman 称 OpenAI 尚未批准该计划。')
    assert translation_errors('Jensen Huang says Nvidia will launch a model.', 'Alex Huang 称 Nvidia 将发布模型。')


def test_existing_altman_display_is_preserved():
    from src.processors.news_presentation import present, voice_text
    source = "OpenAI CEO Sam Altman says people need to 'accept some bad things' for the benefits of AI - NBC News"
    translated = 'OpenAI CEO Sam Altman 表示，人们需要为 AI 的好处“接受一些坏事” - NBC News'
    assert not translation_errors(source, translated)
    assert voice_text(present(translated, source_name='NBC News', original_text=source).text, '奥特曼') == '人们需要为 AI 的好处“接受一些坏事”'


@pytest.mark.parametrize('source,translated', [
    ("Berkshire Hathaway Says It's Now America's 4th-Largest Homebuilder. It Also Owns More Than 10% of Lennar.", '伯克希尔称它现在是美国第4大住宅建造商。它还持有 Lennar 超过10%的股份。'),
    ('Microsoft says it will launch a model.', '微软称它将发布一个模型。'),
])
def test_corporate_attribution_keeps_issuer_identity_across_languages(source, translated):
    assert not translation_errors(source, translated)
    assert translation_errors(source, translated.replace('伯克希尔', '微软').replace('微软称', '谷歌称'))


@pytest.mark.parametrize('source,translated,theme', [
    ('Google buys nuclear energy.', 'Google 购买核能。', 'resource-procurement'),
    ('Microsoft purchased power under long-term contracts.', 'Microsoft 按长期合同购买电力。', 'resource-procurement'),
    ('Mastercard signs agreements with resellers to expand distribution channels.', 'Mastercard 与经销商签署协议，扩展分销渠道。', 'commercial-distribution'),
    ('TSMC confirms talks about new semiconductor factories.', 'TSMC 确认关于新半导体工厂的谈判。', 'strategic-negotiation'),
])
def test_watchpoint_families_cover_inflections_not_a_single_headline(source, translated, theme):
    assert _rule_for({'excerpt': source, 'output_text': translated}).key == theme
