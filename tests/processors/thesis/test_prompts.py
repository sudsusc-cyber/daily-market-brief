"""test_prompts.py — Judgment Ledger prompt input shaping"""

from dataclasses import dataclass

from src.processors.thesis.prompts import MAX_ACTIVE_THEMES_IN_PROMPT, build_user_prompt


@dataclass
class MockFootnote:
    index: int
    source: str
    url: str


@dataclass
class MockSummary:
    summary_html: str
    footnotes: list[MockFootnote]


@dataclass
class MockFrontierPoint:
    lab: str
    text: str
    related_tickers: list[str]
    source_url: str
    source_name: str
    score: int


def test_company_summary_prompt_uses_filtered_summary_not_raw_bundle_shape() -> None:
    summary = MockSummary(
        summary_html="<div><strong>微软</strong> —— Azure 需求继续受 AI 工作负载支撑。<sup>[1]</sup></div>",
        footnotes=[MockFootnote(1, "Reuters", "https://example.com/msft")],
    )

    prompt = build_user_prompt(company_news=summary)

    assert "持仓公司新闻摘要" in prompt
    assert "Azure 需求继续受 AI 工作负载支撑" in prompt
    assert "Reuters https://example.com/msft" in prompt
    assert "### ?" not in prompt


def test_macro_summary_prompt_uses_filtered_summary() -> None:
    summary = MockSummary(
        summary_html="<p><strong>美联储。</strong>CPI 数据改变降息预期。<sup>[1]</sup></p>",
        footnotes=[MockFootnote(1, "WSJ", "https://example.com/cpi")],
    )

    prompt = build_user_prompt(macro_news=summary)

    assert "宏观视野保留条目" in prompt
    assert "CPI 数据改变降息预期" in prompt
    assert "WSJ https://example.com/cpi" in prompt


def test_frontier_labs_prompt_formats_dataclass_items() -> None:
    item = MockFrontierPoint(
        lab="OpenAI",
        text="企业采用提速，继续支撑云与 GPU 需求",
        related_tickers=["MSFT", "NVDA"],
        source_url="https://openai.com/news/x",
        source_name="OpenAI",
        score=5,
    )

    prompt = build_user_prompt(frontier_labs_events=[item])

    assert "Frontier Labs 模块输出" in prompt
    assert "OpenAI: 企业采用提速" in prompt
    assert "url: https://openai.com/news/x" in prompt
    assert "tickers: MSFT,NVDA" in prompt


def test_active_themes_are_capped_in_prompt() -> None:
    themes = [f"theme-{i:03d}" for i in range(MAX_ACTIVE_THEMES_IN_PROMPT + 5)]

    prompt = build_user_prompt(active_themes=themes)

    assert f"`theme-{MAX_ACTIVE_THEMES_IN_PROMPT - 1:03d}`" in prompt
    assert f"`theme-{MAX_ACTIVE_THEMES_IN_PROMPT:03d}`" not in prompt
    assert f"只注入前 {MAX_ACTIVE_THEMES_IN_PROMPT} 个" in prompt
