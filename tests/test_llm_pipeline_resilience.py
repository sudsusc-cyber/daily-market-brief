"""DeepSeek 格式化调用的非思考模式、重试和失败状态回归测试。"""

from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace

from src.collectors.company_news import CompanyNewsBundle, NewsItem
from src.collectors.figures import FigureBundle, FigureMention
from src.collectors.frontier_labs import FrontierBundle, FrontierItem
from src.collectors.sentiment import SentimentBundle, SentimentMetric
from src.collectors.stocks import StockSignal
from src.config import HOLDINGS
from src.processors.figure_filter import filter_one as filter_figure
from src.processors.frontier_labs_filter import filter_all_with_status
from src.processors.holdings_intro import write_intro
from src.processors.llm_client import LLMResponse, LLMUsage
from src.processors.news_summarizer import summarize as summarize_company_news
from src.processors.sentiment_judge import judge
from src.processors.subject.generator import _call_deepseek
from src.processors.thesis.extractor import extract_with_status

_NOW = datetime(2026, 8, 4, tzinfo=UTC)


def _response(text: str | None, error: str | None = None) -> LLMResponse:
    return LLMResponse(text=text, usage=LLMUsage(), error=error)


class _SequenceClient:
    def __init__(self, responses: list[LLMResponse]):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def chat(self, *args, **kwargs):  # noqa: ANN002, ANN003
        self.calls.append(kwargs)
        return self.responses.pop(0)


def _company_bundle() -> CompanyNewsBundle:
    return CompanyNewsBundle(
        holding=HOLDINGS[0],
        items=[NewsItem(
            title="Microsoft 发布重大云服务更新",
            published_at=_NOW,
            url="https://example.com/msft",
            source="Reuters",
        )],
    )


def _figure_bundle() -> FigureBundle:
    return FigureBundle(
        person="巴菲特",
        query="Buffett",
        person_en="Warren Buffett",
        items=[FigureMention(
            title="Buffett said capital allocation will change",
            snippet="He said the company will change its capital allocation.",
            published_at=_NOW,
            url="https://example.com/buffett",
            source="Reuters",
        )],
    )


def _frontier_bundle() -> FrontierBundle:
    return FrontierBundle(
        lab="OpenAI",
        related_tickers=["MSFT", "NVDA"],
        items=[FrontierItem(
            lab="OpenAI",
            title="OpenAI launches a small developer update",
            snippet="The company announced a minor developer tool.",
            published_at=_NOW,
            url="https://example.com/openai",
            source="OpenAI",
            source_type="official",
            related_tickers=["MSFT"],
        )],
    )


def _signal() -> StockSignal:
    return StockSignal(
        holding=HOLDINGS[0],
        last_close=100.0,
        sma_120=95.0,
        sma_200=90.0,
        delta_120=0.05,
        delta_200=0.11,
        signal="NONE",
        error=None,
    )


def _sentiment_bundle() -> SentimentBundle:
    values = [
        ("CNN Fear & Greed", 50.0, ""),
        ("VIX", 20.0, ""),
        ("高收益债利差", 4.0, "%"),
        ("Shiller PE", 25.0, ""),
        ("DXY", 100.0, ""),
    ]
    return SentimentBundle(
        metrics=[
            SentimentMetric(name=name, current=value, prior=value, rating=None, unit=unit)
            for name, value, unit in values
        ],
        fetched_at=_NOW,
    )


def test_company_news_retries_empty_output_without_thinking() -> None:
    client = _SequenceClient([
        _response(None, "ReasoningStarved"),
        _response("<strong>微软</strong> —— 云服务更新。<sup>[1]</sup>"),
    ])

    result = summarize_company_news([_company_bundle()], client=client)

    assert result is not None
    assert "云服务更新" in result.summary_html
    assert len(client.calls) == 2
    assert all(call["thinking"] is False for call in client.calls)


def test_company_news_valid_silence_is_not_a_processing_failure() -> None:
    client = _SequenceClient([_response("持仓今日无重要动态。")])

    result = summarize_company_news([_company_bundle()], client=client)

    assert result is not None
    assert result.is_silence is True
    assert result.summary_html == ""


def test_company_news_returns_failure_only_after_two_attempts() -> None:
    client = _SequenceClient([
        _response(None, "timeout"),
        _response("没有脚注的格式漂移"),
    ])

    assert summarize_company_news([_company_bundle()], client=client) is None
    assert len(client.calls) == 2


def test_figure_retries_incomplete_output_and_marks_true_silence_cleanly() -> None:
    client = _SequenceClient([
        _response("格式漂移，没有编号"),
        _response("▦ 1: no | score=2 | 只是旧闻回顾"),
    ])

    result = filter_figure(_figure_bundle(), client=client)

    assert result.items == []
    assert result.error is None
    assert len(client.calls) == 2
    assert all(call["thinking"] is False for call in client.calls)


def test_figure_failure_remains_distinguishable_from_silence() -> None:
    client = _SequenceClient([
        _response(None, "timeout"),
        _response(None, "timeout"),
    ])

    result = filter_figure(_figure_bundle(), client=client)

    assert result.items == []
    assert result.error == "timeout"


def test_frontier_retries_format_drift_and_reports_final_failure() -> None:
    recovered = _SequenceClient([
        _response("not structured"),
        _response("▦ 1: no | score=2 | 普通开发者工具更新"),
    ])
    points, failures = filter_all_with_status([_frontier_bundle()], client=recovered)
    assert points == []
    assert failures == []
    assert len(recovered.calls) == 2
    assert all(call["thinking"] is False for call in recovered.calls)

    failed = _SequenceClient([
        _response(None, "timeout"),
        _response(None, "timeout"),
    ])
    points, failures = filter_all_with_status([_frontier_bundle()], client=failed)
    assert points == []
    assert len(failures) == 1
    assert failures[0].startswith("OpenAI:")


def test_thesis_retries_invalid_json_and_accepts_valid_empty_array() -> None:
    client = _SequenceClient([
        _response("not json"),
        _response("[]"),
    ])

    evidence, error = extract_with_status(
        client=client,
        company_news=SimpleNamespace(
            summary_html="已筛选的个股摘要包含可核对事实。",
            footnotes=[SimpleNamespace(url="https://example.com/source")],
        ),
        today=date(2026, 8, 4),
    )

    assert evidence == []
    assert error is None
    assert len(client.calls) == 2
    assert all(call["thinking"] is False for call in client.calls)


def test_sentiment_keeps_deterministic_result_when_both_llm_attempts_fail() -> None:
    client = _SequenceClient([
        _response(None, "timeout"),
        _response("invalid json"),
    ])

    result = judge(_sentiment_bundle(), client=client)

    assert result is not None
    assert result["score"] is not None
    assert result["argument_fallback"] is True
    assert "确定性规则" in result["argument"]
    assert all(call["thinking"] is False for call in client.calls)


def test_sentiment_keeps_only_one_sentence_from_llm() -> None:
    client = _SequenceClient([
        _response('{"argument":"VIX 回落至 18.5，风险偏好维持中性。第二句不应保留。"}'),
    ])

    result = judge(_sentiment_bundle(), client=client)

    assert result is not None
    assert result["argument"] == "VIX 回落至 18.5，风险偏好维持中性。"
    assert client.calls[0]["max_tokens"] == 320


def test_holdings_intro_and_subject_disable_thinking() -> None:
    intro_client = _SequenceClient([
        _response(None, "timeout"),
        _response("潮水未至，持仓仍守其位。"),
    ])
    assert write_intro([_signal()], client=intro_client) == "潮水未至，持仓仍守其位。"
    assert len(intro_client.calls) == 2
    assert all(call["thinking"] is False for call in intro_client.calls)

    subject_client = _SequenceClient([_response("风清云定")])
    text, error = _call_deepseek(subject_client, "生成主题")
    assert (text, error) == ("风清云定", None)
    assert subject_client.calls[0]["thinking"] is False
    assert subject_client.calls[0]["max_tokens"] == 256
