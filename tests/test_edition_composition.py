"""Real final-mail regressions plus same-class preservation counterexamples."""
import json
from copy import deepcopy
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.collectors.company_news import NewsItem
from src.processors.edition_composition import compose_company_rows
from src.processors.frontier_labs_filter import FrontierKeyPoint, select_frontier_items
from src.processors.news_presentation import present, replay_presentation
from src.processors.news_selection import factual_excerpt, publishable_excerpt
from src.processors.news_summarizer import _rebuild_safe_summary
from src.processors.thesis.renderer import build_judgment_section, validate_publication
from src.renderer.render import render_email

ROWS = json.loads((Path(__file__).parent / 'fixtures/october1_afternoon_sources.json').read_text())
TODAY = date(2026, 10, 1)


def item(row, ticker=None):
    result = NewsItem(row['original_title'], datetime.fromisoformat(row['published_at']),
                      row['url'], row['source_name'], row['original_summary'], ticker)
    result.source_excerpt = row['excerpt']
    result.translated_excerpt = row['validated_text']
    return result


def company_summary():
    tickers = ['COST', 'NVDA', 'TSM', 'TSM', 'GOOG', 'GOOG', 'KO', '0700.HK', '0700.HK', '9992.HK', '9992.HK']
    names = ['好市多', '英伟达', '台积电', '台积电', '谷歌', '谷歌', '可口可乐', '腾讯', '腾讯', '泡泡玛特', '泡泡玛特']
    items = [item(row, ticker) for row, ticker in zip(ROWS['company'], tickers, strict=True)]
    raw = '\n'.join(f'<strong>{name}</strong> — {row["validated_text"]}[{i}]'
                    for i, (name, row) in enumerate(zip(names, ROWS['company'], strict=True), 1))
    return _rebuild_safe_summary(raw, items)


def test_real_company_mail_keeps_sources_but_merges_lease_and_orders_product():
    summary = company_summary()
    assert summary and len(summary.footnotes) == 11
    assert summary.summary_html.count('租赁10万片') == 1
    assert '租用10万枚' not in summary.summary_html
    assert '扩张可能还会更大' not in summary.summary_html
    assert '2650 亿美元' in summary.summary_html
    assert '(KO)' not in summary.summary_html
    assert 'Coca-Cola' not in summary.summary_html
    assert summary.summary_html.index('Gemini 4 Argon') < summary.summary_html.index('该新模型')
    assert '高性能 GPU 基础设施和推理服务的 AI 原生云' not in summary.summary_html
    for row in summary.evidence:
        assert row['output_text'] == replay_presentation(row['validated_text'], row)
        assert row['output_text'] in summary.summary_html
    lease = next(row for row in summary.evidence if row.get('supporting_sources') and '租赁' in row['output_text'])
    assert lease['supporting_sources'][0]['url'] in {f.url for f in summary.footnotes}


def test_real_model_event_gets_one_specific_judgment_and_final_gate_agrees():
    summary = company_summary()
    sources = {'company_news': [summary]}
    result = build_judgment_section(sources=sources, today=TODAY)
    product = [row for row in result.items if row['theme'] == 'product-commercialization']
    assert len(product) == 1
    assert product[0]['subject'] == '谷歌 Gemini 4 Argon'
    assert any(row['reason'] == 'duplicate_event' for row in result.audit['decisions'])
    assert validate_publication(result, sources=sources, today=TODAY).items == result.items


def test_macro_listicle_recovers_body_not_a_promise_of_ten_reasons():
    row = ROWS['macro'][1]
    source = item(row)
    assert not publishable_excerpt(source, source.title)
    assert factual_excerpt(source) == source.summary


@pytest.mark.parametrize('count', ['Five', '12', 'Seven'])
def test_navigation_grammar_generalizes_without_dropping_factual_counts(count):
    source = SimpleNamespace(title=f'{count} Reasons Bond Yields Are Rising', summary='Bond yields rose across Asia.', source='Source')
    assert factual_excerpt(source) == source.summary
    source.title = 'Five banks raised deposit rates.'
    assert publishable_excerpt(source, source.title)


def test_same_frontier_fact_is_one_row_with_both_labs_and_one_source():
    row = ROWS['frontier'][0]
    def point(lab, text=row['output_text'], url=row['url']):
        return FrontierKeyPoint(lab, text, [], url, row['source_name'], 5)
    original = [point('OpenAI'), point('Anthropic')]
    selected = select_frontier_items(original)
    assert len(selected) == 1 and set(selected[0].labs) == {'OpenAI', 'Anthropic'}
    assert original[0].labs == ()
    assert len(select_frontier_items([point('OpenAI'), point('Anthropic', '另一项调查尚未完成。')])) == 2
    html = render_email(signals=[], generated_at=datetime(2026, 10, 1, tzinfo=UTC), frontier_labs_items=selected)
    assert html.count('FTC 将加快') == 1
    assert 'OpenAI / Anthropic' in html


def test_financing_appositive_drops_description_not_money_or_status():
    assert present('Acme，一家提供云服务的公司，宣布融资 2 亿美元。').text == 'Acme 宣布融资 2 亿美元。'
    for phrase in ['一家拥有 20 个数据中心的公司', '一家尚未获批的公司']:
        assert phrase in present(f'Acme，{phrase}，宣布融资。').text


@pytest.mark.parametrize('other', [
    '甲公司与乙公司签订五年协议，租赁20万片AI芯片。',
    '甲公司与丙公司签订五年协议，租赁10万片AI芯片。',
    '甲公司尚未向乙公司租用10万枚芯片。',
])
def test_lease_changes_are_not_duplicates(other):
    rows = [{'output_text': text} for text in ['甲公司向乙公司租用10万枚芯片。', other]]
    assert len(compose_company_rows(rows, '')) == 2


def test_changed_product_version_still_creates_distinct_watchpoint():
    summary = company_summary()
    row = next(r for r in summary.evidence if 'Google debuts' in r['original_title'])
    changed = deepcopy(row)
    for key in ('original_title', 'original_summary', 'excerpt', 'validated_text', 'output_text'):
        changed[key] = changed[key].replace('Gemini 4', 'Gemini 5')
    changed['url'] = 'https://example.com/new-model'
    obj = SimpleNamespace(summary_html=changed['output_text'], evidence=[changed], footnotes=[SimpleNamespace(url=changed['url'])])
    result = build_judgment_section(sources={'company_news': [summary, obj]}, today=TODAY)
    assert len([row for row in result.items if row['theme'] == 'product-commercialization']) == 2


@pytest.mark.parametrize('issuer', ['Acme', 'Northstar', 'Example'])
def test_expansion_teaser_only_collapses_with_same_baseline_and_geography(issuer):
    broad = {'original_title': f"{issuer}'s $10 Billion U.S. Expansion May Be Getting Even Bigger", 'output_text': '泛化标题'}
    detail = {'original_title': f'{issuer} Reportedly Weighs Texas Factory Investment On Top Of $10B Arizona Push', 'output_text': '具体报道'}
    assert len(compose_company_rows([broad, detail], '')) == 1
    for title in [detail['original_title'].replace('$10B', '$20B'),
                  detail['original_title'].replace('Texas', 'Japan'),
                  detail['original_title'].replace('Weighs', 'Cancels')]:
        assert len(compose_company_rows([broad, dict(detail, original_title=title)], '')) == 2


def test_recovered_macro_body_translation_is_publishable():
    from src.processors.source_grounding import grounded_text
    source = item(ROWS['macro'][1])
    source.source_excerpt = source.summary
    source.translated_excerpt = '全球债券收益率几乎每天都在攀升，引发了有关原因以及还会上涨多少的激烈讨论。'
    text, evidence = grounded_text(source.translated_excerpt, [source])
    assert text and evidence and '十个原因' not in text


@pytest.mark.parametrize('verb', ['climb', 'climbs', 'climbed', 'climbing'])
def test_price_direction_inflections_preserve_opposite_direction_rejection(verb):
    from src.processors.translation_guard import translation_errors
    assert not translation_errors(f'Bond yields {verb}', '债券收益率攀升')
    assert translation_errors(f'Bond yields {verb}', '债券收益率下降')


def test_same_event_alternate_report_does_not_reappear_after_delivery(tmp_path):
    from src.processors.thesis.renderer import commit_publications, load_publications
    summary = company_summary()
    original = build_judgment_section(sources={'company_news': [summary]}, today=TODAY)
    commit_publications(original, tmp_path, today=TODAY)
    # The second source was not selected, so its raw fact hash is different.
    row = next(r for r in summary.evidence if 'Google Unveils' in r['original_title'])
    other = SimpleNamespace(summary_html=row['output_text'], evidence=[row], footnotes=[SimpleNamespace(url=row['url'])])
    assert build_judgment_section(sources={'company_news': [other]}, today=TODAY, history=load_publications(tmp_path)) is None
