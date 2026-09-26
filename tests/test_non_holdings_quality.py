"""Real non-holdings regression: English-only outputs and missing final archive."""

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.collectors.sentiment import SentimentBundle, SentimentMetric
from src.processors import frontier_labs_filter, macro_filter, news_summarizer, translator
from src.processors.sentiment_judge import _argument_supported, _deterministic_argument
from src.processors.source_grounding import grounded_text
from src.processors.translation_guard import translation_errors
from src.sender.smtp_sender import InlineImage
from src.utils.brief_audit import archive_publication, content_report

NOW = datetime(2026, 9, 26, tzinfo=UTC)


@pytest.mark.parametrize('original,translated', [
    ('Fed cuts rates by 25 basis points', '美联储降息 25 基点'),
    ('Microsoft has not received approval', 'Microsoft 尚未获批'),
    ('OpenAI plans to invest $100 billion', 'OpenAI 计划投资 1000 亿美元'),
    ('Treasury yields fall', '美国国债收益率下降'),
    ('Nvidia launches a new model', 'Nvidia 发布新模型'),
])
def test_complete_chinese_translation_can_survive(original, translated):
    assert translation_errors(original, translated) == []
    item = SimpleNamespace(title=original, translated_title=translated, summary='',
                           url='https://example.com/source', published_at=NOW)
    text, mapping = grounded_text(translated, [item])
    assert text == translated
    assert mapping[0]['excerpt'] == original
    assert mapping[0]['output_text'] == translated
    assert mapping[0]['mode'] == 'checked_translation'
    assert len(mapping[0]['source_sha256']) == 64


@pytest.mark.parametrize('original,translated', [
    ('Fed cuts rates by 25 basis points', '美联储降息 50 基点'),
    ('Fed cuts rates by 25 basis points', '美联储降息 25%'),
    ('Microsoft has not received approval', 'Microsoft 已获批准'),
    ('Microsoft has not received approval', 'Microsoft 已获批准，金额 1000 亿美元'),
    ('OpenAI plans to invest $100 billion', 'OpenAI 已投资 1000 亿美元'),
    ('OpenAI invests $100 billion', 'OpenAI 投资 1000 亿港元'),
    ('Microsoft acquires Google', 'Google 收购 Microsoft'),
    ('Microsoft has not received approval, but sales increased', 'Microsoft 已获批准，但收入没有增加'),
])
def test_false_translation_rejected_before_publication(original, translated):
    assert translation_errors(original, translated)
    item = SimpleNamespace(title=original, translated_title=translated, summary='',
                           url='https://example.com/source', published_at=NOW)
    text, mapping = grounded_text(translated, [item])
    assert translated not in text
    assert mapping[0]['mode'] == 'source_extract'


def test_bad_translation_is_retried_and_raw_source_never_mutates():
    responses = iter(['▦ 1: Microsoft 已获批准', '▦ 1: Microsoft 尚未获批'])
    client = SimpleNamespace(chat=lambda *a, **k: SimpleNamespace(text=next(responses), error=None))
    item = SimpleNamespace(title='Microsoft has not received approval', summary='unchanged')
    translator.translate_in_place_news([item], client=client)
    assert item.translated_title == 'Microsoft 尚未获批'
    assert item.title == 'Microsoft has not received approval'
    assert item.summary == 'unchanged'


def test_arbitrary_rewrite_still_cannot_use_translation_as_evidence():
    item = SimpleNamespace(title='Microsoft has not received approval',
        translated_title='Microsoft 尚未获批', summary='', url='https://example.com/source')
    text, mapping = grounded_text('Microsoft 盈利增长 1000 亿美元', [item])
    assert text == 'Microsoft 尚未获批'
    assert mapping[0]['excerpt'] == item.title


def test_input_windows_translate_macro_and_frontier(monkeypatch):
    from src.main import _translate_all_bundles
    calls = []
    monkeypatch.setattr(translator, 'translate_in_place_news', lambda items, **kw: calls.extend(items))
    macro = [SimpleNamespace(title=str(i)) for i in range(9)]
    frontier = [SimpleNamespace(title=str(i)) for i in range(9)]
    _translate_all_bundles(cn_bundles=[], fig_bundles=[], macro_bundles=[SimpleNamespace(items=macro)],
                          frontier_bundles=[SimpleNamespace(items=frontier)], client=None)
    assert calls == macro[:8] + frontier[:8]


def _bundle():
    return SentimentBundle([
        SentimentMetric('VIX', 20, 21, None, observed_at='2026-09-25'),
        SentimentMetric('CNN Fear & Greed', 50, 48, None, observed_at='2026-09-25'),
    ], NOW)


@pytest.mark.parametrize('text', ['VIX 上升至 20，情绪中性。', 'VIX 回落至 50，情绪中性。',
                                  'VIX 降至历史最低水平，情绪中性。', 'VIX 20，情绪极度恐慌。'])
def test_sentiment_wrong_direction_value_history_or_verdict_rejected(text):
    assert not _argument_supported(text, _bundle(), '中性')


def test_sentiment_supported_sentence_and_useful_fallback():
    assert _argument_supported('VIX 回落至 20，情绪中性。', _bundle(), '中性')
    text = _deterministic_argument(_bundle(), '中性')
    assert 'VIX 20' in text and '50' in text and '中性' in text
    _bundle_stale = _bundle()
    _bundle_stale.metrics[0].stale_from = '2026-09-20'
    assert not _argument_supported('VIX 回落至 20，情绪中性。', _bundle_stale, '中性')
    assert '沿用 2026-09-20' in _deterministic_argument(_bundle_stale, '中性')


def test_archive_includes_exact_mail_and_offline_image_preview(tmp_path, monkeypatch):
    monkeypatch.setenv('BRIEF_AUDIT_DIR', str(tmp_path / 'audit'))
    monkeypatch.setenv('GH_RUN_ID', '123')
    monkeypatch.setenv('GH_RUN_ATTEMPT', '2')
    image = tmp_path / 'logo.png'
    image.write_bytes(b'fixture-image')
    html = '<p>中文新闻</p><img src="cid:public_logo">'
    report = {'status': 'verified', 'counts': {}, 'news_coverage': {}}
    directory = archive_publication(html, generated_at=NOW, report=report,
                                    inline_images=[InlineImage('public_logo', image)])
    assert directory.name == '123-2-2026-09-26'
    assert (directory / 'email.html').read_text() == html
    assert 'cid:' not in (directory / 'preview.html').read_text()
    manifest = json.loads((directory / 'manifest.json').read_text())
    assert manifest['delivery']['status'] == 'not_sent'
    assert (directory / manifest['images'][0]['path']).read_bytes() == image.read_bytes()


def test_quality_detects_english_only_outputs_even_when_citations_exist():
    evidence = [{'url': 'https://example.com', 'mode': 'verified_extract', 'output_text': 'Original English title'}]
    report = content_report(signals=[], valuations={}, sentiment=None, expected_tickers=[],
                            news={'macro': (1, [SimpleNamespace(evidence=evidence)])})
    assert report['status'] == 'degraded'
    assert report['news_coverage']['macro']['non_chinese_outputs'] == 1


def test_processors_receive_both_source_and_translation():
    item = SimpleNamespace(title='Microsoft has not received approval',
        translated_title='Microsoft 尚未获批', summary='original summary', snippet='original snippet',
        source='Reuters', source_type='official', url='https://example.com/source')
    company = SimpleNamespace(error=None, holding=SimpleNamespace(ticker='MSFT', name='Microsoft'), items=[item])
    macro = SimpleNamespace(error=None, source='Reuters', items=[item])
    outputs = [news_summarizer._format_input([company])[0], macro_filter._format_input([macro])[0],
               frontier_labs_filter._format_input([item])]
    for payload in outputs:
        assert item.title in payload
        assert item.translated_title in payload


def test_formal_preview_restores_history_but_cannot_save_daily_state():
    workflow = Path('.github/workflows/formal-test-send.yml').read_text()
    assert 'BRIEF_PREVIEW_ONLY: ${{ inputs.preview_only }}' in workflow
    assert 'preview-no-smtp' in workflow
    assert 'include-hidden-files: true' in workflow
    assert 'GH_RUN_ATTEMPT: ${{ github.run_attempt }}' in workflow
    saves = workflow.split('uses: actions/cache/save@')[1:]
    assert all('daily-state-' not in block.split('- name:', 1)[0] for block in saves)


@pytest.mark.parametrize('original,translated', [
    ('Qualcomm (QCOM) Renews its Global Patent License With Apple (AAPL)',
     'Qualcomm (QCOM) 与 Apple (AAPL) 续签全球专利许可协议'),
    ('Google Launches Project Suncatcher Oct. 1', 'Google 于10月1日发射 Project Suncatcher'),
    ('Microsoft plans to invest over US$10b by 2030.', 'Microsoft 计划在2030年前投资超过100亿美元。'),
    ('U.S., China Agree to Trim Tariffs, Start AI Dialogue', 'U.S. 与 China 同意削减关税，启动 AI 对话'),
    ('Mastercard Rolls Out SoFiUSD Settlement', 'Mastercard 推出 SoFiUSD 结算'),
    ('Microsoft will launch a satellite on Oct. 1, 2026.', 'Microsoft 将于2026年10月1日发射一颗卫星。'),
])
def test_actual_preview_translation_false_positives(original, translated):
    assert not translation_errors(original, translated)


def test_planned_and_future_actions_still_cannot_become_completed():
    assert translation_errors('Microsoft will acquire Google', 'Microsoft 已收购 Google')
    assert translation_errors('Google launches satellite Oct. 1', 'Google 在10月2日发射卫星')


def test_question_headline_uses_complete_operating_fact_without_mutating_source():
    from src.processors.news_selection import company_candidate, factual_excerpt
    item = SimpleNamespace(title='Costco (COST) vs Walmart (WMT): Which is a Better Stock to Buy?',
        summary='Costco reported fourth-quarter results on September 24. Earnings came in at $6.75 a share.',
        url='https://example.com/costco', published_at=NOW)
    raw_title = item.title
    assert company_candidate(item, 'COST')
    excerpt = factual_excerpt(item)
    item.source_excerpt = excerpt
    item.translated_excerpt = '每股收益为6.75美元。'
    assert not translation_errors(excerpt, item.translated_excerpt)
    text, mapping = grounded_text('Which stock to buy?', [item])
    assert text == item.translated_excerpt
    assert mapping[0]['excerpt'] == 'Earnings came in at $6.75 a share.'
    assert mapping[0]['original_title'] == raw_title == item.title


def test_routine_sovereign_ratings_are_not_moodys_company_news():
    from src.processors.news_selection import company_candidate
    for title in ('Moody’s Ratings affirms Iceland’s A1 ratings, maintains stable outlook',
                  'Moody’s cuts Botswana credit rating to Baa2', 'Moody’s lifts Montenegro’s credit rating to Ba2'):
        assert not company_candidate(SimpleNamespace(title=title, summary=''), 'MCO')
    assert company_candidate(SimpleNamespace(title='Moody’s reports record quarterly revenue', summary=''), 'MCO')


def test_price_commentary_and_generic_praise_do_not_fill_sections():
    from src.processors.news_selection import frontier_candidate, meaningful_quote
    title = "Why Akamai Technologies (AKAM) Is Up 9.0% After Massive Anthropic AI Cloud Deal - And What's Next"
    assert not frontier_candidate(SimpleNamespace(title=title, snippet=title))
    assert frontier_candidate(SimpleNamespace(title='Anthropic announces new cloud infrastructure agreement', snippet=''))
    assert not meaningful_quote(SimpleNamespace(title="Microsoft CEO says streamlining is great to see", snippet=''))
    assert meaningful_quote(SimpleNamespace(title='CEO said capex will rise to $10 billion', snippet=''))


def test_source_apostrophes_are_escaped_once_and_html_remains_safe():
    from bs4 import BeautifulSoup

    from src.collectors.company_news import NewsItem
    item = NewsItem("Moody's revenue rose", NOW, 'https://example.com/mco', 'Source')
    summary = news_summarizer._rebuild_safe_summary("<strong>穆迪</strong>——Moody's revenue rose[1]", [item])
    assert "Moody's" in BeautifulSoup(summary.summary_html, 'html.parser').get_text()
    assert '&amp;#x27;' not in summary.summary_html
    item.title = 'A &amp; B &lt;img src=x onerror=alert(1)&gt; revenue rose'
    text, _ = grounded_text('unmatched', [item])
    assert '<img' not in text and '&amp;' not in text


def test_latin_identifiers_adjacent_to_chinese_are_not_missing_entities():
    assert not translation_errors('Microsoft invests $10 billion in AI', 'Microsoft在AI领域投资100亿美元')
    assert translation_errors('Microsoft invests $10 billion in AI', 'Google在AI领域投资100亿美元')


def test_all_editorial_noise_is_valid_silence_not_llm_failure():
    from src.collectors.company_news import CompanyNewsBundle, NewsItem
    from src.config import HOLDINGS
    holding = next(h for h in HOLDINGS if h.ticker == 'MCO')
    item = NewsItem('Moody’s affirms Iceland’s A1 ratings', NOW, 'https://example.com', 'Source')
    result = news_summarizer.summarize([CompanyNewsBundle(holding, [item])], client=None)
    assert result.is_silence and not result.summary_html


def test_thesis_receives_exact_published_translation_without_company_wrapper():
    from src.processors.thesis.prompts import _format_summary_block
    summary = SimpleNamespace(summary_html='<div>微软│Microsoft 尚未获批[1]</div>', footnotes=[],
        evidence=[{'output_text': 'Microsoft 尚未获批', 'url': 'https://example.com/msft'}])
    payload = '\n'.join(_format_summary_block(summary))
    assert 'text=Microsoft 尚未获批 | url=https://example.com/msft' in payload
