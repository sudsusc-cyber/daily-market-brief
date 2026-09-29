from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from bs4 import BeautifulSoup

from src.collectors.figures import FigureBundle, FigureMention
from src.collectors.sentiment import SentimentBundle, SentimentMetric
from src.processors.figure_filter import FigureSummary, _parse_output, assign_footnotes
from src.processors.news_presentation import publication_text
from src.processors.source_grounding import grounded_text
from src.processors.thesis.extractor import _grounding_material
from src.renderer.render import render_email
from src.utils.brief_audit import content_report

NOW = datetime(2026, 9, 26, 7, tzinfo=UTC)


@pytest.mark.parametrize('source,sentence,expected', [
    ('Windows Report', 'Xbox 有很棒的游戏，但 Satya Nadella 称商业模式必须改变 - Windows Report',
     'Xbox 有很棒的游戏，但萨提亚·纳德拉称商业模式必须改变'),
    ('wsj.com', 'Anthropic 将支付 116 亿美元 - wsj.com', 'Anthropic 将支付 116 亿美元'),
    ('WSJ', 'Anthropic 将支付 116 亿美元 — 华尔街日报', 'Anthropic 将支付 116 亿美元'),
    ('Reuters', '据 Reuters 报道，Apple 尚未获批 - Reuters', '据 Reuters 报道，Apple 尚未获批'),
    ('Reuters', 'Apple - 尚未获批', 'Apple - 尚未获批'),
    ('Windows Report', 'Windows Report 报道了 Xbox 消息', 'Windows Report 报道了 Xbox 消息'),
    ('', 'Apple 宣布计划（金额 1,000 美元，预计明年）', 'Apple 宣布计划（金额 1,000 美元，预计明年）'),
    ('Yahoo', 'Microsoft (NasdaqGS:MSFT) 计划投资 100 亿美元', 'Microsoft 计划投资 100 亿美元'),
    ('Yahoo', 'Mastercard (MA) 刚刚启用稳定币结算', 'Mastercard 刚刚启用稳定币结算'),
    ('Yahoo', '人工智能 (AI) 收入增长 25%', '人工智能 (AI) 收入增长 25%'),
])
def test_presentation_removes_only_listing_and_publisher_metadata(source, sentence, expected):
    assert publication_text(sentence, source_name=source) == expected


def test_qualcomm_prose_keeps_full_verified_translation_and_raw_evidence():
    original = 'QUALCOMM Incorporated (NASDAQ:QCOM) said on September 24 that it renewed its global patent license agreement with Apple Inc. (NASDAQ:AAPL).'
    translated = 'QUALCOMM Incorporated (NASDAQ:QCOM) 于 9 月 24 日表示,已与 Apple Inc. (NASDAQ:AAPL) 续签全球专利许可协议。'
    item = SimpleNamespace(title=original, summary='', source='Yahoo', url='https://example.com/qcom',
                           translated_title=translated, published_at=NOW)
    text, evidence = grounded_text(translated, [item])
    assert text == '高通于 9 月 24 日表示，已与苹果续签全球专利许可协议。'
    assert evidence[0]['validated_text'] == translated
    assert evidence[0]['original_title'] == original == item.title
    assert evidence[0]['excerpt'] == original
    assert evidence[0]['output_text'] == text
    assert evidence[0]['source_name'] == 'Yahoo'

    def material():
        return _grounding_material(company_news=SimpleNamespace(summary_html=text, evidence=evidence),
                                   macro_news=None, figure_summaries=[], frontier_labs_events=[])
    assert material()['company_news']['urls'] == {item.url}
    evidence[0]['output_text'] = text.replace('9 月 24 日', '9 月 25 日')
    assert material() == {}


def test_figure_body_excludes_publisher_but_footnote_remains():
    original = 'Xbox Has Great Games, but Satya Nadella Says the Business Model Must Change - Windows Report'
    translated = 'Xbox 有很棒的游戏，但 Satya Nadella 称商业模式必须改变 - Windows Report'
    mention = FigureMention(original, '', NOW, 'https://example.com/xbox', 'Windows Report', translated)
    points = _parse_output('▦ 1: yes | score=4 | ' + translated, [mention])
    assert len(points) == 1
    assert 'Windows Report' not in points[0].text
    summary = FigureSummary(person='纳德拉', person_en='Satya Nadella', items=points)
    html = render_email(signals=[], generated_at=NOW,
                        figures=[FigureBundle(person='纳德拉', query='', items=[mention])],
                        figure_summaries=[summary], figure_footnotes=assign_footnotes([summary]))
    soup = BeautifulSoup(html, 'html.parser')
    assert '- Windows Report' not in soup.get_text()
    assert any('Windows Report' in a.get_text() and a.get('href') == mention.url for a in soup.find_all('a'))
    assert '[1]' in soup.get_text()


def test_sentiment_normal_dates_are_archived_but_only_carried_date_is_visible():
    bundle = SentimentBundle([
        SentimentMetric('VIX', 14.87, 15.67, None, observed_at='2026-09-25', source='CBOE'),
        SentimentMetric('DXY', 101, 100, None, observed_at='2026-09-23', stale_from='2026-09-23', source='Yahoo'),
    ], NOW)
    html = render_email(signals=[], generated_at=NOW, sentiment=bundle)
    text = BeautifulSoup(html, 'html.parser').get_text()
    assert '2026-09-25' not in text
    assert '沿用 2026-09-23' in text
    report = content_report(signals=[], valuations={}, sentiment=bundle, news={}, expected_tickers=[])
    assert report['observations'][0]['observed_at'] == '2026-09-25'
    assert report['observations'][0]['source'] == 'CBOE'
