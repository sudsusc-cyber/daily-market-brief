"""tests/test_subject_extractor.py — 主题数据提取测试。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from src.processors.subject.extractor import (
    _extract_first_news,
    _extract_mood,
    _extract_signals,
    extract_subject_data,
)


# Mock 数据结构
@dataclass
class MockHolding:
    ticker: str


@dataclass
class MockSignal:
    holding: MockHolding
    signal: str  # NONE / DCA / LUMP_SUM


@dataclass
class MockMetric:
    name: str
    current: float | None = None
    error: str | None = None


@dataclass
class MockBundle:
    metrics: list[MockMetric]


@dataclass
class MockSummary:
    summary_html: str


# ───────────────  Mood  ───────────────

class TestExtractMood:
    def test_basic(self) -> None:
        verdict = {"verdict": "今日情绪 · 偏热", "score": 65.5}
        bundle = MockBundle([
            MockMetric(name="CNN Fear & Greed", current=63.43),
            MockMetric(name="VIX", current=18.05),
            MockMetric(name="DXY", current=98.5),  # 不参与
        ])
        mood = _extract_mood(verdict, bundle)
        assert mood.label == "偏热"
        assert mood.score == 65.5
        assert mood.cnn_fear_greed == 63.43
        assert mood.vix == 18.05

    def test_failed_metrics_none(self) -> None:
        verdict = {"verdict": "今日情绪 · 中性", "score": 50.0}
        bundle = MockBundle([
            MockMetric(name="CNN Fear & Greed", current=None, error="API down"),
            MockMetric(name="VIX", current=18.0),
        ])
        mood = _extract_mood(verdict, bundle)
        assert mood.cnn_fear_greed is None
        assert mood.vix == 18.0

    def test_no_verdict_defaults(self) -> None:
        mood = _extract_mood(None, None)
        assert mood.label == "中性"
        assert mood.score == 50.0


# ───────────────  Signals  ───────────────

class TestExtractSignals:
    def test_mixed(self) -> None:
        signals = [
            MockSignal(MockHolding("MSFT"), "NONE"),
            MockSignal(MockHolding("MCO"), "DCA"),
            MockSignal(MockHolding("AAPL"), "DCA"),
            MockSignal(MockHolding("KO"), "LUMP_SUM"),
        ]
        s = _extract_signals(signals)
        assert s.dca_count == 2
        assert s.lump_sum_count == 1
        assert sorted(s.dca_tickers) == ["AAPL", "MCO"]
        assert s.lump_sum_tickers == ["KO"]

    def test_all_none(self) -> None:
        signals = [MockSignal(MockHolding("MSFT"), "NONE")]
        s = _extract_signals(signals)
        assert s.dca_count == 0
        assert s.lump_sum_count == 0

    def test_empty(self) -> None:
        s = _extract_signals([])
        assert s.dca_count == 0


# ───────────────  News  ───────────────

class TestExtractFirstNews:
    def test_kicker_format(self) -> None:
        """昨日动态格式:公司名 + 竖线 + 摘要 + 脚注"""
        s = MockSummary(summary_html=(
            '<div style="margin:0 0 10px 0; font-family:..."><span style="color:#7A1F2B">苹果</span>'
            '<span style="color:#D9D2BE">│</span>'
            'App Store 抽成案被驳回,案件移交最高法院。<sup>[1]</sup></div>'
            '<div>第二条不该被取到</div>'
        ))
        text = _extract_first_news(s)
        assert text is not None
        assert "苹果" in text
        assert "App Store" in text
        assert "第二条不该被取到" not in text

    def test_truncation(self) -> None:
        long_text = "极长摘要" * 30  # > 80 字
        s = MockSummary(summary_html=f'<div>{long_text}</div>')
        text = _extract_first_news(s)
        assert text and len(text) <= 81  # 80 字 + "…"

    def test_macro_paragraph_format(self) -> None:
        """宏观加工器输出 <p>,邮件主题也必须能读到首条。"""
        s = MockSummary(summary_html=(
            '<p style="margin:0"><span style="color:#7A1F2B">美联储。</span>'
            '决策者维持利率不变。<sup><a href="https://example.com">[1]</a></sup></p>'
            '<p>第二条不该被取到</p>'
        ))

        text = _extract_first_news(s)

        assert text is not None
        assert "美联储" in text
        assert "维持利率不变" in text
        assert "第二条不该被取到" not in text

    def test_empty(self) -> None:
        assert _extract_first_news(None) is None
        assert _extract_first_news(MockSummary(summary_html="")) is None


# ───────────────  Full integration  ───────────────

class TestExtractSubjectData:
    def test_today_2026_05_01(self) -> None:
        data = extract_subject_data(
            today_bj=date(2026, 5, 1),
            signals=[MockSignal(MockHolding("MCO"), "DCA")],
            sentiment_verdict={"verdict": "今日情绪 · 偏热", "score": 65.5},
            sentiment_bundle=MockBundle([
                MockMetric("CNN Fear & Greed", 63.43),
                MockMetric("VIX", 18.05),
            ]),
            company_news_summary=MockSummary(
                '<div><span>苹果</span><span>│</span>App Store 案上诉至最高法院。</div>'
            ),
            macro_news_summary=MockSummary(
                '<p>布伦特原油创战后新高,布伦特价格突破 130 美元。</p>'
            ),
            email_html="<html><body><h1>朝闻录</h1><p>正文段落很长很长...</p></body></html>",
        )
        assert data.solar_term.current == "谷雨"
        assert data.mood.label == "偏热"
        assert data.signals.dca_count == 1
        assert data.signals.dca_tickers == ["MCO"]
        assert "苹果" in data.holdings_news_top1
        assert "布伦特" in data.macro_news_top1
        assert "朝闻录" in data.email_full_text
        assert "<" not in data.email_full_text  # 已去 tags

    def test_to_dict(self) -> None:
        data = extract_subject_data(
            today_bj=date(2026, 1, 25),
            signals=[],
            sentiment_verdict={"verdict": "今日情绪 · 偏冷", "score": 35.0},
            sentiment_bundle=None,
            company_news_summary=None,
            macro_news_summary=None,
            email_html="<p>test</p>",
        )
        d = data.to_dict()
        assert d["solar_term"]["current"] == "大寒"
        assert d["mood"]["label"] == "偏冷"
        assert d["signals"]["dca_count"] == 0
        assert d["holdings_news_top1"] is None

    def test_text_truncation(self) -> None:
        long_html = "<p>" + "正" * 3000 + "</p>"
        data = extract_subject_data(
            today_bj=date(2026, 5, 1),
            signals=None,
            sentiment_verdict=None,
            sentiment_bundle=None,
            company_news_summary=None,
            macro_news_summary=None,
            email_html=long_html,
        )
        # 默认截 2000 字 + "…"
        assert len(data.email_full_text) <= 2001
        assert data.email_full_text.endswith("…")
