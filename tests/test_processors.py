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
from src.processors.figure_filter import FigureKeyPoint, FigureSummary
from src.processors.figure_filter import _format_input as fig_format
from src.processors.figure_filter import _parse_output as fig_parse
from src.processors.figure_filter import select_voice_summaries
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
            "▦ 1: yes | score=4 | 黄仁勋说算力是未来\n▦ 2: no | score=2 | 是他人转述",
            items,
        )
        assert len(out) == 1
        assert out[0].text == "黄仁勋说算力是未来"
        assert out[0].source_url == "https://a"

    def test_parse_output_skips_invalid_index(self) -> None:
        items = [
            FigureMention(title="A", snippet="", published_at=_utc(2026, 4, 30), url="https://a", source="X"),
        ]
        out = fig_parse("▦ 1: yes | score=4 | ok\n▦ 99: yes | score=4 | out_of_range", items)
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
        out = fig_parse("▦ 1,2,3: yes | score=5 | AI 推理需求增长远超预期", items)
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
            "▦ 1: yes | score=4 | 算力是未来的核心资产\n▦ 2: yes | score=4 |  算力是未来的核心资产 ",
            items,
        )
        assert len(out) == 1

    def test_parse_output_merge_takes_first_valid_idx(self) -> None:
        # 合并索引 "5,2" 中第一个 5 越界,应回退到第二个 2
        items = [
            FigureMention(title="A", snippet="", published_at=_utc(2026, 4, 30), url="https://a", source="X"),
            FigureMention(title="B", snippet="", published_at=_utc(2026, 4, 30), url="https://b", source="Y"),
        ]
        out = fig_parse("▦ 5,2: yes | score=4 | 观点", items)
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
            "pe": "Shiller PE", "dxy": "DXY",
        }
        units = {"hy": "%", "fg": "", "vix": "", "pe": "", "dxy": ""}
        metrics = [
            SentimentMetric(name=names[k], current=vals.get(k), prior=None,
                           rating=None, unit=units[k])
            for k in names
        ]
        return SentimentBundle(metrics=metrics, fetched_at=_utc(2026, 4, 30))

    def test_extreme_fear_low_score(self) -> None:
        # 全部指标都极度恐慌
        out = score_sentiment(self._bundle(fg=10, vix=35, hy=7, pe=12, dxy=110))
        assert out is not None
        assert out["score"] < 25
        assert out["verdict"] == "极度恐慌"

    def test_extreme_greed_high_score(self) -> None:
        # 全部指标都极度贪婪
        out = score_sentiment(self._bundle(fg=92, vix=10, hy=2, pe=38, dxy=92))
        assert out is not None
        assert out["score"] > 75
        assert out["verdict"] == "极度贪婪"

    def test_neutral_middle(self) -> None:
        out = score_sentiment(self._bundle(fg=50, vix=20, hy=4, pe=25, dxy=100))
        assert out is not None
        assert 40 <= out["score"] <= 60
        assert out["verdict"] == "中性"

    def test_deterministic_same_input(self) -> None:
        # 同一输入运行 5 次结果完全一致
        results = [
            score_sentiment(self._bundle(fg=63, vix=18, hy=3.5, pe=27, dxy=103))
            for _ in range(5)
        ]
        assert len({r["score"] for r in results}) == 1
        assert len({r["verdict"] for r in results}) == 1

    def test_partial_failure_renormalizes(self) -> None:
        # 三个指标失败,剩两个仍出结果
        b = SentimentBundle(metrics=[
            SentimentMetric(name="CNN Fear & Greed", current=70, prior=None, rating=None),
            SentimentMetric(name="VIX", current=14, prior=None, rating=None),
            SentimentMetric(name="高收益债利差", current=None, prior=None, rating=None,
                           unit="%", error="网络错误"),
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
            "▦ 1: yes | score=4 | 不安全 URL 应被丢\n▦ 2: yes | score=4 | 安全的留下",
            items,
        )
        assert len(out) == 1, "javascript: 协议应被 is_safe_url 拦截"
        assert out[0].source_url == "https://safe.example.com/b"

    def test_parse_output_drops_data_url(self) -> None:
        items = [
            FigureMention(title="A", snippet="", published_at=_utc(2026, 4, 30),
                         url="data:text/html,<script>alert(1)</script>", source="X"),
        ]
        out = fig_parse("▦ 1: yes | score=4 | unused", items)
        assert out == []

    def test_parse_output_drops_empty_url(self) -> None:
        items = [
            FigureMention(title="A", snippet="", published_at=_utc(2026, 4, 30),
                         url="", source="X"),
        ]
        out = fig_parse("▦ 1: yes | score=4 | unused", items)
        assert out == []


class TestFigureFilterScore:
    """质量评分解析:score >= 4 保留, < 4 丢弃, 合并仍正常。"""

    def test_parse_score_4_keeps(self) -> None:
        items = [
            FigureMention(title="A", snippet="", published_at=_utc(2026, 4, 30),
                         url="https://a", source="Reuters"),
        ]
        out = fig_parse("▦ 1: yes | score=4 | AI 推理需求增长远超预期", items)
        assert len(out) == 1
        assert out[0].score == 4
        assert out[0].text == "AI 推理需求增长远超预期"

    def test_parse_score_5_keeps(self) -> None:
        items = [
            FigureMention(title="A", snippet="", published_at=_utc(2026, 4, 30),
                         url="https://a", source="Bloomberg"),
        ]
        out = fig_parse("▦ 1: yes | score=5 | 重大资本配置转向", items)
        assert len(out) == 1
        assert out[0].score == 5

    def test_parse_score_3_discards(self) -> None:
        items = [
            FigureMention(title="A", snippet="", published_at=_utc(2026, 4, 30),
                         url="https://a", source="X"),
        ]
        out = fig_parse("▦ 1: yes | score=3 | 一般性行业评论", items)
        assert len(out) == 0, "score=3 应被丢弃"

    def test_parse_score_2_discards(self) -> None:
        items = [
            FigureMention(title="A", snippet="", published_at=_utc(2026, 4, 30),
                         url="https://a", source="X"),
        ]
        out = fig_parse("▦ 1: yes | score=2 | 客户需求强劲", items)
        assert len(out) == 0

    def test_parse_score_missing_discards(self) -> None:
        """score 缺失 → 丢弃,不再默认放行。"""
        items = [
            FigureMention(title="A", snippet="", published_at=_utc(2026, 4, 30),
                         url="https://a", source="Reuters"),
        ]
        out = fig_parse("▦ 1: yes | 没有 score 字段应被丢弃", items)
        assert len(out) == 0, "缺失 score 的 yes 行必须丢弃"

    def test_parse_score_zero_discards(self) -> None:
        items = [
            FigureMention(title="A", snippet="", published_at=_utc(2026, 4, 30),
                         url="https://a", source="X"),
        ]
        out = fig_parse("▦ 1: yes | score=0 | score 越界", items)
        assert len(out) == 0

    def test_parse_score_six_discards(self) -> None:
        items = [
            FigureMention(title="A", snippet="", published_at=_utc(2026, 4, 30),
                         url="https://a", source="X"),
        ]
        out = fig_parse("▦ 1: yes | score=6 | score 越界", items)
        assert len(out) == 0

    def test_merge_with_scores(self) -> None:
        items = [
            FigureMention(title="A", snippet="", published_at=_utc(2026, 4, 30),
                         url="https://a", source="Reuters"),
            FigureMention(title="B", snippet="", published_at=_utc(2026, 4, 30),
                         url="https://b", source="Bloomberg"),
        ]
        out = fig_parse("▦ 1,2: yes | score=5 | 合并后的重大判断", items)
        assert len(out) == 1
        assert out[0].score == 5
        assert out[0].source_url == "https://a"

    def test_mixed_scores_only_keeps_high(self) -> None:
        items = [
            FigureMention(title="A", snippet="", published_at=_utc(2026, 4, 30),
                         url="https://a", source="Reuters"),
            FigureMention(title="B", snippet="", published_at=_utc(2026, 4, 30),
                         url="https://b", source="CNBC"),
            FigureMention(title="C", snippet="", published_at=_utc(2026, 4, 30),
                         url="https://c", source="WSJ"),
        ]
        out = fig_parse(
            "▦ 1: yes | score=5 | 重要判断\n"
            "▦ 2: yes | score=2 | 空洞口号\n"
            "▦ 3: yes | score=4 | 有用信息",
            items,
        )
        assert len(out) == 2
        scores = {kp.score for kp in out}
        assert scores == {4, 5}

    def test_published_at_stored(self) -> None:
        dt = _utc(2026, 4, 30)
        items = [
            FigureMention(title="A", snippet="", published_at=dt,
                         url="https://a", source="Reuters"),
        ]
        out = fig_parse("▦ 1: yes | score=4 | 观点", items)
        assert len(out) == 1
        assert out[0].published_at == dt


class TestVoiceThrottling:
    """版面限流:最多 3 位人物,每人最多 1 条,优先级排序。"""

    def _summary(self, person: str, texts_and_scores: list[tuple[str, int]],
                 source: str = "Reuters") -> FigureSummary:
        pub = _utc(2026, 5, 4)
        items = [
            FigureKeyPoint(
                text=t, source_url="https://x", source_name=source,
                score=s, published_at=pub,
            )
            for t, s in texts_and_scores
        ]
        return FigureSummary(person=person, person_en=person, items=items)

    def test_limits_to_three_figures(self) -> None:
        summaries = [
            self._summary("巴菲特", [("巴菲特观点", 4)]),
            self._summary("苏妈", [("苏妈观点", 4)]),
            self._summary("纳德拉", [("纳德拉观点", 4)]),
            self._summary("奥特曼", [("奥特曼观点", 4)]),
            self._summary("但斌", [("但斌观点", 4)]),
        ]
        out = select_voice_summaries(summaries)
        assert len(out) == 3
        assert out[0].person == "巴菲特"

    def test_limits_one_item_per_figure(self) -> None:
        summaries = [
            self._summary("黄仁勋", [("观点A", 5), ("观点B", 4)]),
        ]
        out = select_voice_summaries(summaries)
        assert len(out) == 1
        assert len(out[0].items) == 1
        assert out[0].items[0].score == 5

    def test_score_5_beats_score_4(self) -> None:
        """score=5 优先于 score=4,不管人物优先级。"""
        summaries = [
            self._summary("黄仁勋", [("老黄观点", 4)]),  # P0
            self._summary("但斌", [("但斌观点", 5)]),    # P2,但 score 更高
        ]
        out = select_voice_summaries(summaries)
        assert out[0].person == "但斌", "score=5 应排在 score=4 前面"

    def test_same_priority_higher_score_first(self) -> None:
        summaries = [
            self._summary("苏妈", [("苏妈观点", 4)]),
            self._summary("Hock Tan", [("陈福阳观点", 5)]),
        ]
        out = select_voice_summaries(summaries)
        assert out[0].person == "Hock Tan", "同优先级,score 高的在前"

    def test_source_authority_tiebreaker(self) -> None:
        dt = _utc(2026, 5, 4)
        s1 = FigureSummary(person="苏妈", person_en="Lisa Su", items=[
            FigureKeyPoint(text="观点", source_url="https://x", source_name="CNBC",
                          score=5, published_at=dt),
        ])
        s2 = FigureSummary(person="Hock Tan", person_en="Hock Tan", items=[
            FigureKeyPoint(text="观点", source_url="https://x", source_name="Reuters",
                          score=5, published_at=dt),
        ])
        out = select_voice_summaries([s1, s2])
        assert out[0].person == "Hock Tan"

    def test_newer_first_tiebreaker(self) -> None:
        s1 = FigureSummary(person="苏妈", person_en="Lisa Su", items=[
            FigureKeyPoint(text="观点", source_url="https://x", source_name="Reuters",
                          score=5, published_at=_utc(2026, 5, 4)),
        ])
        s2 = FigureSummary(person="Hock Tan", person_en="Hock Tan", items=[
            FigureKeyPoint(text="观点", source_url="https://x", source_name="Reuters",
                          score=5, published_at=_utc(2026, 5, 3)),
        ])
        out = select_voice_summaries([s2, s1])
        assert out[0].person == "苏妈"

    def test_removes_empty_summaries(self) -> None:
        summaries = [
            self._summary("黄仁勋", [("观点", 4)]),
            FigureSummary(person="苏妈", person_en="Lisa Su", items=[]),
            self._summary("巴菲特", [("观点", 5)]),
        ]
        out = select_voice_summaries(summaries)
        assert len(out) == 2
        persons = {s.person for s in out}
        assert "苏妈" not in persons

    def test_chinese_display_name_priority(self) -> None:
        """同 score 时,中文 display name 也能命中 _FIGURE_PRIORITY。"""
        summaries = [
            self._summary("奥特曼", [("观点", 4)]),  # P2
            self._summary("黄仁勋", [("观点", 4)]),  # P0
        ]
        out = select_voice_summaries(summaries)
        # 同 score=4, P0 优先于 P2
        assert out[0].person == "黄仁勋"

    def test_all_empty_still_empty(self) -> None:
        summaries = [
            FigureSummary(person="黄仁勋", person_en="Jensen Huang", items=[]),
            FigureSummary(person="但斌", person_en="Dan Bin", items=[]),
        ]
        out = select_voice_summaries(summaries)
        assert out == []


# ── 官方源补充层测试 ──

import xml.etree.ElementTree as ET
from unittest import mock

from src.collectors.figure_official_sources import (
    OFFICIAL_SOURCES,
    OfficialSource,
    _entry_to_mention,
    _fetch_rss_entries,
    _fetch_berkshire_entries,
    _parse_entry_date,
    _person_matches,
    fetch_all,
)


def _make_rss_xml(entries: list[dict]) -> bytes:
    """构建最小 RSS 2.0 XML 用于测试。"""
    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")
    for e in entries:
        item = ET.SubElement(channel, "item")
        for tag, val in e.items():
            el = ET.SubElement(item, tag)
            el.text = str(val)
    return ET.tostring(rss, encoding="utf-8")


def _utc_dt(*args: int) -> datetime:
    """快捷构造 UTC datetime。"""
    from datetime import UTC
    return datetime(*args, tzinfo=UTC)


class TestOfficialSourceParsing:
    """RSS/HTML 原始抓取测试。"""

    def test_rss_entries_parsed(self) -> None:
        xml = _make_rss_xml([
            {"title": "OpenAI announces GPT-6", "link": "https://openai.com/gpt6",
             "description": "Sam Altman introduced GPT-6 at the developer conference."},
        ])
        with mock.patch("requests.get") as m_get:
            m_get.return_value.status_code = 200
            m_get.return_value.content = xml
            m_get.return_value.raise_for_status = lambda: None
            entries = _fetch_rss_entries("https://openai.com/news/rss.xml")
        assert len(entries) == 1
        assert entries[0]["title"] == "OpenAI announces GPT-6"
        assert "GPT-6" in entries[0]["summary"]

    def test_rss_entries_multiple(self) -> None:
        xml = _make_rss_xml([
            {"title": "OpenAI announces GPT-6", "link": "https://openai.com/gpt6", "description": "..."},
            {"title": "OpenAI partners with Microsoft", "link": "https://openai.com/msft", "description": "..."},
        ])
        with mock.patch("requests.get") as m_get:
            m_get.return_value.status_code = 200
            m_get.return_value.content = xml
            m_get.return_value.raise_for_status = lambda: None
            entries = _fetch_rss_entries("https://openai.com/news/rss.xml")
        assert len(entries) == 2

    def test_berkshire_html_parsed(self) -> None:
        html = """<html><body>
        <a href="news0426.html">Warren Buffett's 2026 Shareholder Letter</a>
        <a href="news0322.html">Greg Abel on Insurance Operations</a>
        <a href="#top">Back to top</a>
        </body></html>"""
        with mock.patch("requests.get") as m_get:
            m_get.return_value.status_code = 200
            m_get.return_value.content = html.encode()
            m_get.return_value.raise_for_status = lambda: None
            entries = _fetch_berkshire_entries("https://www.berkshirehathaway.com/news/2026news.html")
        assert len(entries) == 2
        assert any("Warren Buffett" in e["title"] for e in entries)
        assert any("Greg Abel" in e["title"] for e in entries)

    def test_berkshire_skips_anchor_links(self) -> None:
        html = """<html><body>
        <a href="#top">Back to top</a>
        <a href="#footer">Footer</a>
        </body></html>"""
        with mock.patch("requests.get") as m_get:
            m_get.return_value.status_code = 200
            m_get.return_value.content = html.encode()
            m_get.return_value.raise_for_status = lambda: None
            entries = _fetch_berkshire_entries("https://www.berkshirehathaway.com/news/2026news.html")
        assert len(entries) == 0

    def test_entry_to_mention_converts_published(self) -> None:
        entry = {
            "title": "Test",
            "link": "https://example.com",
            "summary": "Summary text",
            "published_parsed": (2026, 5, 4, 9, 0, 0, 0, 0, 0),
        }
        mention = _entry_to_mention(entry, "OpenAI")
        assert mention.title == "Test"
        assert mention.url == "https://example.com"
        assert mention.snippet == "Summary text"
        assert mention.source == "OpenAI"
        assert mention.published_at.year == 2026


class TestPersonMatching:
    """逐条目人物匹配：text 必须命中 aliases 才归入对应人物。"""

    def test_aliases_hit(self) -> None:
        assert _person_matches("Sam Altman announced GPT-6", ["Sam Altman", "Altman", "奥特曼"])

    def test_aliases_chinese_hit(self) -> None:
        assert _person_matches("奥特曼发布新模型", ["Sam Altman", "Altman", "奥特曼"])

    def test_aliases_case_insensitive(self) -> None:
        assert _person_matches("sam altman speaks at conference", ["Sam Altman", "Altman"])

    def test_aliases_partial_hit_in_text(self) -> None:
        assert _person_matches("CEO Satya Nadella discussed Azure growth", ["Satya Nadella", "Nadella", "纳德拉"])

    def test_aliases_miss(self) -> None:
        assert not _person_matches("OpenAI releases new API endpoint", ["Sam Altman", "Altman", "奥特曼"])

    def test_aliases_miss_unrelated_person(self) -> None:
        assert not _person_matches("Jensen Huang keynote at GTC", ["Sam Altman", "Altman"])

    def test_100_unrelated_entries_none_attributed(self) -> None:
        """模拟 OpenAI RSS 100 条无关 entry，不应全部归入奥特曼。"""
        entries = [
            {"title": f"OpenAI product update #{i}", "link": "https://openai.com/p{i}",
             "summary": "New API features",
             "published_parsed": (2026, 5, 4, 10, 0, 0, 0, 0, 0)}
            for i in range(100)
        ]
        # 只有 1 条提到 Sam Altman
        entries.append({
            "title": "Sam Altman speaks at AI Summit",
            "link": "https://openai.com/altman-summit",
            "summary": "Altman discussed AGI timeline",
            "published_parsed": (2026, 5, 4, 11, 0, 0, 0, 0, 0),
        })
        src = OfficialSource("OpenAI", "https://openai.com/rss",
                             aliases={"奥特曼": ["Sam Altman", "Altman", "奥特曼"]},
                             parser_type="rss")
        with mock.patch("src.collectors.figure_official_sources._fetch_rss_entries", return_value=entries):
            with mock.patch("src.collectors.figure_official_sources.OFFICIAL_SOURCES", [src]):
                result = fetch_all(
                    _utc_dt(2026, 5, 4, 0, 0),
                    _utc_dt(2026, 5, 5, 0, 0),
                )
        assert "奥特曼" in result
        assert len(result["奥特曼"]) == 1  # 只有提到 Sam Altman 的那条


class TestDateFiltering:
    """官方源日期过滤：老日期/缺日期丢弃，不回退 datetime.now()。"""

    def test_old_rss_date_discarded(self) -> None:
        xml = _make_rss_xml([
            {"title": "Satya Nadella said Azure is growing", "link": "https://blogs.microsoft.com/1",
             "description": "Nadella noted cloud demand",
             "pubDate": "Mon, 28 Apr 2026 10:00:00 GMT"},  # 6 天前,不在 24h 窗口
        ])
        src = OfficialSource("Microsoft Blog", "https://blogs.microsoft.com/feed/",
                             aliases={"纳德拉": ["Satya Nadella", "Nadella", "纳德拉"]},
                             parser_type="rss")
        with mock.patch("requests.get") as m_get:
            m_get.return_value.status_code = 200
            m_get.return_value.content = xml
            m_get.return_value.raise_for_status = lambda: None
            with mock.patch("src.collectors.figure_official_sources.OFFICIAL_SOURCES", [src]):
                result = fetch_all(
                    _utc_dt(2026, 5, 4, 0, 0),  # 窗口从 5/4 开始
                    _utc_dt(2026, 5, 5, 0, 0),
                )
        assert "纳德拉" not in result or len(result.get("纳德拉", [])) == 0

    def test_in_window_rss_date_kept(self) -> None:
        xml = _make_rss_xml([
            {"title": "Satya Nadella said Azure is growing", "link": "https://blogs.microsoft.com/1",
             "description": "Nadella noted cloud demand",
             "pubDate": "Sun, 04 May 2026 10:00:00 GMT"},  # 在 24h 窗口内
        ])
        src = OfficialSource("Microsoft Blog", "https://blogs.microsoft.com/feed/",
                             aliases={"纳德拉": ["Satya Nadella", "Nadella", "纳德拉"]},
                             parser_type="rss")
        with mock.patch("requests.get") as m_get:
            m_get.return_value.status_code = 200
            m_get.return_value.content = xml
            m_get.return_value.raise_for_status = lambda: None
            with mock.patch("src.collectors.figure_official_sources.OFFICIAL_SOURCES", [src]):
                result = fetch_all(
                    _utc_dt(2026, 5, 4, 0, 0),
                    _utc_dt(2026, 5, 5, 0, 0),
                )
        assert "纳德拉" in result
        assert len(result["纳德拉"]) == 1

    def test_missing_date_entry_discarded(self) -> None:
        """RSS 条目没有 published_parsed 且 URL 无法解析日期 → 丢弃。"""
        xml = _make_rss_xml([
            {"title": "Lisa Su talks Zen 6", "link": "https://ir.amd.com/generic",
             "description": "Dr. Su discussed roadmap",
             # 无 published_parsed,URL 无日期
             },
        ])
        src = OfficialSource("AMD IR", "https://ir.amd.com/rss",
                             aliases={"苏妈": ["Lisa Su", "Dr. Su", "苏妈"]},
                             parser_type="rss")
        with mock.patch("requests.get") as m_get:
            m_get.return_value.status_code = 200
            m_get.return_value.content = xml
            m_get.return_value.raise_for_status = lambda: None
            with mock.patch("src.collectors.figure_official_sources.OFFICIAL_SOURCES", [src]):
                result = fetch_all(
                    _utc_dt(2026, 5, 4, 0, 0),
                    _utc_dt(2026, 5, 5, 0, 0),
                )
        assert "苏妈" not in result or len(result.get("苏妈", [])) == 0

    def test_url_date_parsed_as_fallback(self) -> None:
        """published_parsed 缺失但 URL 含日期 → 解析成功。"""
        entry = {
            "title": "AMD Reports First Quarter Results",
            "link": "https://ir.amd.com/2026/05/04/amd-q1-results",
            "summary": "Lisa Su commented on Q1 performance",
            "published_parsed": None,
        }
        dt = _parse_entry_date(entry)
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 5
        assert dt.day == 4

    def test_parse_entry_date_returns_none_for_unparseable(self) -> None:
        entry = {"title": "No date", "link": "https://example.com/about", "summary": "", "published_parsed": None}
        assert _parse_entry_date(entry) is None

    def test_berkshire_url_date_parsed(self) -> None:
        entry = {"title": "Buffett letter", "link": "https://www.berkshirehathaway.com/news/news0426.html",
                 "summary": "", "published_parsed": None}
        dt = _parse_entry_date(entry, default_year=2026)
        assert dt is not None
        assert dt.month == 4
        assert dt.day == 26
        assert dt.year == 2026

    def test_berkshire_url_date_without_default_year_returns_none(self) -> None:
        """无 default_year 时 Berkshire 短链接无法确定年份,应返回 None。"""
        entry = {"title": "Buffett letter", "link": "https://www.berkshirehathaway.com/news/news0426.html",
                 "summary": "", "published_parsed": None}
        dt = _parse_entry_date(entry)  # 未传 default_year
        assert dt is None


class TestFetchAllWithAliases:
    """fetch_all 端到端：人物匹配 + 日期过滤 + 时间窗口。"""

    def test_entry_without_person_name_discarded(self) -> None:
        xml = _make_rss_xml([
            {"title": "OpenAI launches new enterprise tier", "link": "https://openai.com/enterprise",
             "description": "New features for business customers",
             "pubDate": "Sun, 04 May 2026 10:00:00 GMT"},
        ])
        src = OfficialSource("OpenAI", "https://openai.com/rss",
                             aliases={"奥特曼": ["Sam Altman", "Altman", "奥特曼"]},
                             parser_type="rss")
        with mock.patch("requests.get") as m_get:
            m_get.return_value.status_code = 200
            m_get.return_value.content = xml
            m_get.return_value.raise_for_status = lambda: None
            with mock.patch("src.collectors.figure_official_sources.OFFICIAL_SOURCES", [src]):
                result = fetch_all(
                    _utc_dt(2026, 5, 4, 0, 0),
                    _utc_dt(2026, 5, 5, 0, 0),
                )
        assert "奥特曼" not in result or len(result.get("奥特曼", [])) == 0

    def test_entry_with_person_and_date_in_window_kept(self) -> None:
        xml = _make_rss_xml([
            {"title": "Sam Altman speaks at AI conference",
             "link": "https://openai.com/altman-ai-summit",
             "description": "Altman said AGI is closer than expected",
             "pubDate": "Sun, 04 May 2026 14:00:00 GMT"},
        ])
        src = OfficialSource("OpenAI", "https://openai.com/rss",
                             aliases={"奥特曼": ["Sam Altman", "Altman", "奥特曼"]},
                             parser_type="rss")
        with mock.patch("requests.get") as m_get:
            m_get.return_value.status_code = 200
            m_get.return_value.content = xml
            m_get.return_value.raise_for_status = lambda: None
            with mock.patch("src.collectors.figure_official_sources.OFFICIAL_SOURCES", [src]):
                result = fetch_all(
                    _utc_dt(2026, 5, 4, 0, 0),
                    _utc_dt(2026, 5, 5, 0, 0),
                )
        assert "奥特曼" in result
        assert len(result["奥特曼"]) == 1
        assert "speaks" in result["奥特曼"][0].title

    def test_multi_person_source_splits_correctly(self) -> None:
        """Berkshire 源同一条目可同时归入巴菲特和阿贝尔。"""
        html = """<html><body>
        <a href="https://www.berkshirehathaway.com/news/2026/05/04/letter.html">
        Warren Buffett and Greg Abel discuss succession plan</a>
        </body></html>"""
        src = OfficialSource("Berkshire Hathaway", "https://www.berkshirehathaway.com/news/2026news.html",
                             aliases={
                                 "巴菲特": ["Warren Buffett", "Buffett", "巴菲特"],
                                 "阿贝尔": ["Greg Abel", "Abel", "阿贝尔"],
                             },
                             parser_type="html")
        with mock.patch("requests.get") as m_get:
            m_get.return_value.status_code = 200
            m_get.return_value.content = html.encode()
            m_get.return_value.raise_for_status = lambda: None
            with mock.patch("src.collectors.figure_official_sources.OFFICIAL_SOURCES", [src]):
                result = fetch_all(
                    _utc_dt(2026, 5, 4, 0, 0),
                    _utc_dt(2026, 5, 5, 0, 0),
                )
        assert "巴菲特" in result
        assert "阿贝尔" in result
        assert len(result["巴菲特"]) == 1
        assert len(result["阿贝尔"]) == 1


class TestOfficialSourceIsolation:
    def test_fetch_all_handles_single_failure(self) -> None:
        """一个源挂掉不阻断其他源。"""
        sources = [
            OfficialSource("Good", "https://good.com/rss",
                           aliases={"人物A": ["Person A", "A"]},
                           parser_type="rss"),
            OfficialSource("Bad", "https://bad.com/rss",
                           aliases={"人物B": ["Person B", "B"]},
                           parser_type="rss"),
        ]
        xml = _make_rss_xml([
            {"title": "Person A said something", "link": "https://good.com/1",
             "description": "test", "pubDate": "Sun, 04 May 2026 10:00:00 GMT"},
        ])

        def fake_get(url, **kwargs):
            m = mock.MagicMock()
            if "bad" in url:
                m.raise_for_status.side_effect = Exception("Connection refused")
                return m
            m.status_code = 200
            m.content = xml
            m.raise_for_status = lambda: None
            return m

        with mock.patch("requests.get", side_effect=fake_get):
            with mock.patch("src.collectors.figure_official_sources.OFFICIAL_SOURCES", sources):
                result = fetch_all(
                    _utc_dt(2026, 5, 4, 0, 0),
                    _utc_dt(2026, 5, 5, 0, 0),
                )
        assert "人物A" in result
        assert len(result["人物A"]) == 1
        assert "人物B" not in result


class TestOfficialSourceAuthority:
    """官方源权威排名测试。"""

    def test_official_source_ranks_before_reuters(self) -> None:
        from src.processors.figure_filter import _SOURCE_AUTHORITY
        assert _SOURCE_AUTHORITY["OpenAI"] == 0
        assert _SOURCE_AUTHORITY["Microsoft Blog"] == 0
        assert _SOURCE_AUTHORITY["AMD IR"] == 0
        assert _SOURCE_AUTHORITY["Berkshire Hathaway"] == 0
        assert _SOURCE_AUTHORITY["OpenAI"] < _SOURCE_AUTHORITY["Reuters"]
        assert _SOURCE_AUTHORITY["Berkshire Hathaway"] < _SOURCE_AUTHORITY["Bloomberg"]

    def test_reuters_still_before_bloomberg(self) -> None:
        from src.processors.figure_filter import _SOURCE_AUTHORITY
        assert _SOURCE_AUTHORITY["Reuters"] < _SOURCE_AUTHORITY["Bloomberg"]

    def test_unknown_source_defaults_to_five(self) -> None:
        from src.processors.figure_filter import _SOURCE_AUTHORITY
        assert _SOURCE_AUTHORITY.get("Unknown Blog", 5) == 5


class TestOfficialSourceMerge:
    """官方源合并进 figures.py: pushed 去重 + 优先官方源作为代表来源。"""

    def test_dedup_same_title_hash_uses_official_source(self) -> None:
        from src.collectors.figures import _content_hash
        person = "奥特曼"
        google_item = FigureMention(
            title="Sam Altman on AI safety",
            snippet="...",
            published_at=_utc(2026, 5, 4),
            url="https://reuters.com/altman-ai",
            source="Reuters",
        )
        official_item = FigureMention(
            title="Sam Altman on AI safety",
            snippet="...",
            published_at=_utc(2026, 5, 4),
            url="https://openai.com/blog/ai-safety",
            source="OpenAI",
        )
        gh = _content_hash(person, google_item)
        oh = _content_hash(person, official_item)
        assert gh == oh  # 归一化后标题一致,hash 也应一致

    def test_unique_official_items_get_new_hash(self) -> None:
        from src.collectors.figures import _content_hash
        person = "苏妈"
        google_item = FigureMention(
            title="AMD beats Q1 estimates",
            snippet="...",
            published_at=_utc(2026, 5, 3),
            url="https://reuters.com/amd-q1",
            source="Reuters",
        )
        official_item = FigureMention(
            title="AMD Reports First Quarter 2026 Financial Results",
            snippet="...",
            published_at=_utc(2026, 5, 3),
            url="https://ir.amd.com/press-release",
            source="AMD IR",
        )
        gh = _content_hash(person, google_item)
        oh = _content_hash(person, official_item)
        assert gh != oh
