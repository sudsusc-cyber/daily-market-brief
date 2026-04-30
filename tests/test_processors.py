"""单元测试:processor 内部纯函数(格式化输入、解析输出)。

不调用 DeepSeek API。
"""

from __future__ import annotations

from datetime import datetime, timezone

from src.collectors.company_news import CompanyNewsBundle, NewsItem
from src.collectors.figures import FigureBundle, FigureMention
from src.collectors.macro_news import MacroFeedBundle, MacroNewsItem
from src.collectors.sentiment import SentimentBundle, SentimentMetric
from src.collectors.stocks import StockSignal  # noqa: F401  确保 import 不破坏
from src.config import HOLDINGS
from src.processors.figure_filter import _format_input as fig_format
from src.processors.figure_filter import _parse_output as fig_parse
from src.processors.macro_filter import _format_input as macro_format
from src.processors.news_summarizer import _format_input as news_format
from src.processors.sentiment_judge import _format_input as sent_format
from src.processors.sentiment_judge import _parse_json as sent_parse_json
from src.processors.translator import _is_chinese, _parse_lines


def _utc(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, 12, 0, tzinfo=timezone.utc)


class TestTranslator:
    def test_is_chinese(self) -> None:
        assert _is_chinese("腾讯三季报")
        assert not _is_chinese("Microsoft beats earnings")
        assert _is_chinese("Apple 发布新 iPhone")  # 含中文

    def test_parse_lines(self) -> None:
        text = "▦ 1: 微软发布新版本\n▦ 2: 苹果财报亮眼\nrandom noise\n▦ 3: NVIDIA 订单激增"
        out = _parse_lines(text)
        assert out == {1: "微软发布新版本", 2: "苹果财报亮眼", 3: "NVIDIA 订单激增"}

    def test_parse_lines_skips_garbage(self) -> None:
        assert _parse_lines("hello world\n no number here") == {}


class TestNewsSummarizerFormat:
    def test_format_input(self) -> None:
        nvda = HOLDINGS[3]  # NVIDIA
        msft = HOLDINGS[0]  # Microsoft
        b1 = CompanyNewsBundle(holding=nvda, items=[
            NewsItem(title="Nvidia 发布 Rubin", published_at=_utc(2026, 4, 30), url="", source="Reuters"),
        ])
        b2 = CompanyNewsBundle(holding=msft, items=[])  # 无新闻 → 不出现
        b3 = CompanyNewsBundle(holding=HOLDINGS[2], error="API 故障")  # error → 不出现
        s, flat = news_format([b1, b2, b3])
        # M4 内修复后:无新闻 / error 的公司直接跳过(不写"无")
        assert "英伟达" in s  # 中文译名提示
        assert "Nvidia 发布 Rubin" in s
        assert "Reuters" in s
        assert "MSFT" not in s  # 无新闻应被跳过
        assert "AAPL" not in s  # error 应被跳过
        assert flat == [b1.items[0]]
        # flat_items 用于 LLM [N] 引用回查 url


class TestMacroFilterFormat:
    def test_format_input_skips_errors_and_empty(self) -> None:
        b1 = MacroFeedBundle(source="WSJ", items=[
            MacroNewsItem(title="Fed 降息 25 bp", published_at=_utc(2026, 4, 30), url="", source="WSJ"),
        ])
        b2 = MacroFeedBundle(source="Reuters", error="SSL 失败")
        b3 = MacroFeedBundle(source="FT", items=[])
        s, flat = macro_format([b1, b2, b3])
        assert "【WSJ】" in s
        assert "Fed 降息 25 bp" in s
        assert "Reuters" not in s
        assert "FT" not in s
        assert flat == [b1.items[0]]


class TestFigureFilter:
    def test_format_input(self) -> None:
        items = [
            FigureMention(
                title="Jensen Huang says AI demand surging",
                snippet="At GTC, Huang told reporters that ...",
                published_at=_utc(2026, 4, 29),
                url="https://x", source="Reuters",
            ),
        ]
        s = fig_format(items)
        assert "▦ 1:" in s
        assert "标题=Jensen Huang says" in s
        assert "Reuters" in s

    def test_parse_output_keeps_yes_drops_no(self) -> None:
        items = [
            FigureMention(title="A", snippet="", published_at=_utc(2026, 4, 30), url="https://a", source="X"),
            FigureMention(title="B", snippet="", published_at=_utc(2026, 4, 30), url="https://b", source="Y"),
        ]
        out = fig_parse(
            "▦ 1: yes | 黄仁勋说算力是未来\n▦ 2: no | 是他人转述",
            items,
        )
        assert len(out) == 1
        assert out[0].text == "黄仁勋说算力是未来"
        assert out[0].source_url == "https://a"

    def test_parse_output_skips_invalid_index(self) -> None:
        items = [
            FigureMention(title="A", snippet="", published_at=_utc(2026, 4, 30), url="https://a", source="X"),
        ]
        out = fig_parse("▦ 1: yes | ok\n▦ 99: yes | out_of_range", items)
        assert len(out) == 1


class TestSentimentJudge:
    def test_format_input(self) -> None:
        b = SentimentBundle(metrics=[
            SentimentMetric(name="VIX", current=18.5, prior=16.0, rating=None),
            SentimentMetric(name="CNN F&G", current=63.0, prior=70.0, rating="greed"),
            SentimentMetric(name="HY 利差", current=None, prior=None, rating=None,
                           unit="%", error="FRED 故障"),
        ], fetched_at=_utc(2026, 4, 30))
        s = sent_format(b)
        assert "VIX" in s
        assert "18.50" in s
        assert "[greed]" in s
        assert "数据获取失败" in s

    def test_parse_clean_json(self) -> None:
        out = sent_parse_json('{"verdict":"今日情绪 · 偏热","argument":"VIX 低位..."}')
        assert out is not None
        assert out["verdict"] == "今日情绪 · 偏热"

    def test_parse_markdown_wrapped_json(self) -> None:
        # 模型偶尔会包 ```json
        text = '```json\n{"verdict":"中性","argument":"指标分歧"}\n```'
        out = sent_parse_json(text)
        assert out is not None
        assert out["verdict"] == "中性"

    def test_parse_invalid_returns_none(self) -> None:
        assert sent_parse_json("hello world") is None
