"""单元测试:processor 内部纯函数(格式化输入、解析输出)。

不调用 DeepSeek API。
"""

from __future__ import annotations

from datetime import UTC, datetime

from src.collectors.company_news import CompanyNewsBundle, NewsItem
from src.collectors.figures import FigureMention
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
from src.processors.sentiment_judge import score_sentiment
from src.processors.translator import _is_chinese, _parse_lines


def _utc(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, 12, 0, tzinfo=UTC)


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

    def test_parse_output_merges_indices(self) -> None:
        # LLM 把 1, 2, 3 合并为同一观点(同一场演讲不同媒体报道)
        items = [
            FigureMention(title="A", snippet="", published_at=_utc(2026, 4, 30),
                         url="https://reuters/a", source="Reuters"),
            FigureMention(title="B", snippet="", published_at=_utc(2026, 4, 30),
                         url="https://bbg/b", source="Bloomberg"),
            FigureMention(title="C", snippet="", published_at=_utc(2026, 4, 30),
                         url="https://cnbc/c", source="CNBC"),
        ]
        out = fig_parse("▦ 1,2,3: yes | AI 推理需求增长远超预期", items)
        assert len(out) == 1
        # 主索引 1 应作为代表来源
        assert out[0].source_url == "https://reuters/a"
        assert out[0].source_name == "Reuters"
        assert out[0].text == "AI 推理需求增长远超预期"

    def test_parse_output_dedupe_same_text(self) -> None:
        # 二重保险:LLM 误输出两条同样观点(空格差异),仍只保留一条
        items = [
            FigureMention(title="A", snippet="", published_at=_utc(2026, 4, 30), url="https://a", source="X"),
            FigureMention(title="B", snippet="", published_at=_utc(2026, 4, 30), url="https://b", source="Y"),
        ]
        out = fig_parse(
            "▦ 1: yes | 算力是未来的核心资产\n▦ 2: yes |  算力是未来的核心资产 ",
            items,
        )
        assert len(out) == 1

    def test_parse_output_merge_takes_first_valid_idx(self) -> None:
        # 合并索引 "5,2" 中第一个 5 越界,应回退到第二个 2
        items = [
            FigureMention(title="A", snippet="", published_at=_utc(2026, 4, 30), url="https://a", source="X"),
            FigureMention(title="B", snippet="", published_at=_utc(2026, 4, 30), url="https://b", source="Y"),
        ]
        out = fig_parse("▦ 5,2: yes | 观点", items)
        assert len(out) == 1
        assert out[0].source_url == "https://b"


class TestSentimentJudge:
    def test_format_input(self) -> None:
        b = SentimentBundle(metrics=[
            SentimentMetric(name="VIX", current=18.5, prior=16.0, rating=None),
            SentimentMetric(name="CNN Fear & Greed", current=63.0, prior=70.0, rating="greed"),
            SentimentMetric(name="高收益债利差", current=None, prior=None, rating=None,
                           unit="%", error="FRED 故障"),
        ], fetched_at=_utc(2026, 4, 30))
        s = sent_format(b, "中性", 55.0)
        assert "VIX" in s
        assert "18.50" in s
        assert "[greed]" in s
        assert "数据获取失败" in s
        assert "已固定档位" in s
        assert "中性" in s

    def test_parse_clean_json(self) -> None:
        out = sent_parse_json('{"argument":"VIX 低位 12.3,高收益债利差 3.1% 偏窄,情绪偏热"}')
        assert out is not None
        assert "argument" in out

    def test_parse_markdown_wrapped_json(self) -> None:
        text = '```json\n{"argument":"指标分歧 各拉一头"}\n```'
        out = sent_parse_json(text)
        assert out is not None
        assert out["argument"] == "指标分歧 各拉一头"

    def test_parse_invalid_returns_none(self) -> None:
        assert sent_parse_json("hello world") is None


class TestScoreSentiment:
    """确定性加权打分 — 同输入永远同输出"""

    def _bundle(self, **vals) -> SentimentBundle:
        names = {
            "fg": "CNN Fear & Greed", "vix": "VIX", "hy": "高收益债利差",
            "rsi": "恒指 14 日 RSI", "pe": "Shiller PE", "dxy": "DXY",
        }
        units = {"hy": "%", "fg": "", "vix": "", "rsi": "", "pe": "", "dxy": ""}
        metrics = [
            SentimentMetric(name=names[k], current=vals.get(k), prior=None,
                           rating=None, unit=units[k])
            for k in names
        ]
        return SentimentBundle(metrics=metrics, fetched_at=_utc(2026, 4, 30))

    def test_extreme_fear_low_score(self) -> None:
        # 全部指标都极度恐慌
        out = score_sentiment(self._bundle(fg=10, vix=35, hy=7, rsi=15, pe=12, dxy=110))
        assert out is not None
        assert out["score"] < 25
        assert out["verdict"] == "极度恐慌"

    def test_extreme_greed_high_score(self) -> None:
        # 全部指标都极度贪婪
        out = score_sentiment(self._bundle(fg=92, vix=10, hy=2, rsi=78, pe=38, dxy=92))
        assert out is not None
        assert out["score"] > 75
        assert out["verdict"] == "极度贪婪"

    def test_neutral_middle(self) -> None:
        out = score_sentiment(self._bundle(fg=50, vix=20, hy=4, rsi=50, pe=25, dxy=100))
        assert out is not None
        assert 40 <= out["score"] <= 60
        assert out["verdict"] == "中性"

    def test_deterministic_same_input(self) -> None:
        # 同一输入运行 5 次结果完全一致
        results = [
            score_sentiment(self._bundle(fg=63, vix=18, hy=3.5, rsi=55, pe=27, dxy=103))
            for _ in range(5)
        ]
        assert len({r["score"] for r in results}) == 1
        assert len({r["verdict"] for r in results}) == 1

    def test_partial_failure_renormalizes(self) -> None:
        # 三个指标失败,剩三个仍出结果
        b = SentimentBundle(metrics=[
            SentimentMetric(name="CNN Fear & Greed", current=70, prior=None, rating=None),
            SentimentMetric(name="VIX", current=14, prior=None, rating=None),
            SentimentMetric(name="高收益债利差", current=None, prior=None, rating=None,
                           unit="%", error="网络错误"),
            SentimentMetric(name="恒指 14 日 RSI", current=None, prior=None, rating=None,
                           error="ya"),
            SentimentMetric(name="Shiller PE", current=None, prior=None, rating=None,
                           error="multpl 选择器失败"),
            SentimentMetric(name="DXY", current=None, prior=None, rating=None, error="x"),
        ], fetched_at=_utc(2026, 4, 30))
        out = score_sentiment(b)
        assert out is not None
        # CNN F&G(70 → ~70 score, w=0.25)+ VIX(14 → ~80 score, w=0.25)归一化后 ≈ 75
        assert out["score"] > 60

    def test_all_failed_returns_none(self) -> None:
        b = SentimentBundle(metrics=[
            SentimentMetric(name="CNN Fear & Greed", current=None, prior=None,
                           rating=None, error="x"),
        ], fetched_at=_utc(2026, 4, 30))
        assert score_sentiment(b) is None


class TestFigureFilterUrlSafety:
    """source_url 安全:模板会渲染 <a href="{{ kp.source_url }}">,
    autoescape 不挡 javascript:/data: 协议,必须在 _parse_output 层就过滤。"""

    def test_parse_output_drops_javascript_url(self) -> None:
        items = [
            FigureMention(title="A", snippet="", published_at=_utc(2026, 4, 30),
                         url="javascript:alert(1)", source="X"),
            FigureMention(title="B", snippet="", published_at=_utc(2026, 4, 30),
                         url="https://safe.example.com/b", source="Y"),
        ]
        out = fig_parse(
            "▦ 1: yes | 不安全 URL 应被丢\n▦ 2: yes | 安全的留下",
            items,
        )
        assert len(out) == 1, "javascript: 协议应被 is_safe_url 拦截"
        assert out[0].source_url == "https://safe.example.com/b"

    def test_parse_output_drops_data_url(self) -> None:
        items = [
            FigureMention(title="A", snippet="", published_at=_utc(2026, 4, 30),
                         url="data:text/html,<script>alert(1)</script>", source="X"),
        ]
        out = fig_parse("▦ 1: yes | unused", items)
        assert out == []

    def test_parse_output_drops_empty_url(self) -> None:
        items = [
            FigureMention(title="A", snippet="", published_at=_utc(2026, 4, 30),
                         url="", source="X"),
        ]
        out = fig_parse("▦ 1: yes | unused", items)
        assert out == []
