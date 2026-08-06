"""
完整模拟邮件渲染，包含长期判断区块。
纯本地、零网络、零 LLM。
"""

from __future__ import annotations

import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.processors.thesis.models import ThesisEvidence, ThesisState  # noqa: E402
from src.processors.thesis.renderer import build_judgment_section  # noqa: E402
from src.processors.thesis.rules import run_state_transitions  # noqa: E402


def _d(days_ago: int) -> str:
    return (date.today() - timedelta(days=days_ago)).isoformat()


def _ev(date_str, theme, strength=3, direction="support",
        source_section="company_news", source_name="Reuters",
        tickers=None, horizon="multi_year", eid="", text=""):
    return ThesisEvidence(
        evidence_id=eid or f"{theme}-{date_str}-{source_section}-{source_name}",
        date=date_str,
        source_section=source_section,
        source_name=source_name,
        url=None,
        related_tickers=tickers or ["TEST"],
        theme=theme,
        direction=direction,
        strength=strength,
        horizon=horizon,
        text=text or f"[{date_str}] {source_name}: {theme} 关键事实",
        why_it_matters=f"影响 {theme} 长期假设",
    )


def main():
    today = date.today()
    today_str = today.isoformat()

    # ── 构造已存在的 core 主题 ──
    state: dict[str, ThesisState] = {}

    configs = [
        ("ai-infrastructure-capex", ["MSFT", "NVDA"],
         "AI 基础设施资本开支将持续十年以上", 120, 5, 7, "NVIDIA",
         "超大规模厂商 26Q1 capex 指引再次上调，AI 基础设施投入仍在加速。"),
        ("insurance-pricing-power", ["BRK.B"],
         "保险定价权在经济周期中持续增强", 150, 4, 5, "Berkshire Hathaway",
         "一季度保险浮存收益率扩张，定价周期顶部信号出现。"),
        ("consumer-staples-moat", ["KO", "COST"],
         "必需消费品品牌护城河在通胀期更显价值", 200, 4, 6, "Reuters",
         "本季食品 CPI 抬升期内两家完成完整提价，毛利环比扩张。"),
    ]

    evidence_today: list[ThesisEvidence] = []
    for theme, tickers, one_line, first_days, strength, cnt_90d, src, fact_text in configs:
        state[theme] = ThesisState(
            theme=theme,
            status="core",
            related_tickers=tickers,
            cadence="quarterly",
            stale_after_days=180,
            first_seen=_d(first_days),
            last_evidence_date=_d(5),
            last_strong_evidence_date=_d(15),
            evidence_count_total=12,
            evidence_count_recent_90d=cnt_90d,
            one_line_thesis=one_line,
        )
        evidence_today.append(
            _ev(today_str, theme, strength=strength, direction="support",
                source_name=src, source_section="company_news",
                tickers=tickers, eid=f"ev-{theme}-{today_str}",
                text=fact_text)
        )

    # ── 跑状态机 ──
    holdings_tickers = ["MSFT", "NVDA", "BRK.B", "KO", "COST",
                        "AAPL", "GOOG", "MCO", "AXP", "TSM"]
    new_state, events = run_state_transitions(
        today=today, state=state, recent_evidence=evidence_today,
        holdings_tickers=holdings_tickers,
    )

    section = build_judgment_section(events, state=new_state)

    # ── 渲染为真实 email HTML ──
    from src.collectors.stocks import StockSignal
    from src.config import HOLDINGS
    from src.renderer.render import render_email

    mock_signals = []
    for h in HOLDINGS:
        mock_signals.append(StockSignal(
            h, 100.0, 95.0, 90.0, 0.05, 0.10, "NONE",
        ))

    from src.collectors.sentiment import SentimentBundle, SentimentMetric
    mock_sentiment = SentimentBundle(
        metrics=[
            SentimentMetric(name="CNN Fear & Greed", current=52.0, prior=50.0, rating="neutral"),
            SentimentMetric(name="VIX", current=18.5, prior=19.2, rating=None),
            SentimentMetric(name="DXY", current=98.5, prior=98.3, rating=None),
        ],
        fetched_at=datetime.now(ZoneInfo("UTC")),
    )

    from src.processors.news_summarizer import CompanyNewsSummary, Footnote
    # 模拟 news_summarizer 真实输出:行内 <sup>[N]</sup> + 底部脚注列表
    row_style = (
        "margin:0 0 10px 0;padding:0;"
        "font-family:'Noto Serif SC','Source Han Serif SC','Songti SC','STSong',"
        "Charter,Cambria,Georgia,serif;"
        "font-size:16px;line-height:1.9;color:#1A1A1A;letter-spacing:0.02em;"
    )
    name_style = "color:#7A1F2B;letter-spacing:0.04em;"
    sep_style = "color:#D9D2BE;margin:0 6px;"
    fn_style = (
        "color:#0563C1;text-decoration:none;font-family:Charter,Georgia,serif;"
        "font-size:11px;font-style:normal;"
    )
    mock_cn_summary = CompanyNewsSummary(
        summary_html=(
            f'<div style="{row_style}">'
            f'<span style="{name_style}">英伟达</span>'
            f'<span style="{sep_style}">│</span>'
            f'Blackwell GPU 量产进度超预期，资本开支指引上调。'
            f'<sup><a href="https://reuters.com/1" target="_blank" rel="noopener" style="{fn_style}">[1]</a></sup>'
            f'</div>'
            f'<div style="{row_style}">'
            f'<span style="{name_style}">苹果</span>'
            f'<span style="{sep_style}">│</span>'
            f'App Store 抽成案被最高法院驳回。'
            f'<sup><a href="https://reuters.com/2" target="_blank" rel="noopener" style="{fn_style}">[2]</a></sup>'
            f'</div>'
        ),
        footnotes=[
            Footnote(index=1, url="https://reuters.com/1", source="Reuters"),
            Footnote(index=2, url="https://reuters.com/2", source="Reuters"),
        ],
    )

    from src.processors.frontier_labs_filter import FrontierKeyPoint
    mock_frontier_labs = [
        FrontierKeyPoint(
            lab="OpenAI",
            text="新一代推理模型发布，企业 API 调用量周环比增长 40%，云基础设施需求持续扩张。",
            related_tickers=["MSFT", "NVDA"],
            source_url="https://openai.com/news/model-x",
            source_name="OpenAI",
            score=5,
        ),
        FrontierKeyPoint(
            lab="Anthropic",
            text="获得新一轮 35 亿美元融资，将主要用于扩大算力集群规模。",
            related_tickers=["GOOG", "NVDA"],
            source_url="https://anthropic.com/blog/series-e",
            source_name="Anthropic",
            score=4,
        ),
    ]

    html = render_email(
        signals=mock_signals,
        generated_at=datetime.now(ZoneInfo("Asia/Shanghai")),
        logo_cids={},
        header_image_url="https://images.pexels.com/photos/691668/pexels-photo-691668.jpeg"
                         "?auto=compress&cs=tinysrgb&w=1280&h=640&fit=crop",
        holdings_intro=None,
        sentiment=mock_sentiment,
        sentiment_verdict=None,
        company_news=[1],
        company_news_summary=mock_cn_summary,
        figures=None,
        figure_summaries=None,
        figure_silence_note=None,
        figure_footnotes=None,
        macro_news=None,
        macro_news_summary=None,
        buffett_13f=None,
        frontier_labs_items=mock_frontier_labs,
        judgment_section=section,
    )

    out = Path(tempfile.gettempdir()) / "email_preview_with_judgment.html"
    out.write_text(html, encoding="utf-8")
    print(f"HTML 已写入 {out} ({len(html):,} bytes)")
    print(f"file://{out}")

    # ── 终端输出 ──
    print()
    print("═" * 66)
    print("  长期判断  |  JUDGMENT LEDGER")
    print("═" * 66)
    if section:
        for i, item in enumerate(section.items, 1):
            marker = f" · {item['marker']}" if item["marker"] else ""
            print(f"  [{i}] 「{item['thesis']}」{marker}")
    else:
        print("  (尚无成熟的长期判断)")
    print("═" * 66)

    # 状态一览
    print()
    print(f"{'主题':36} {'状态':10} {'90d证据':>8}  {'最近证据':>12}")
    print("-" * 70)
    for theme, st in sorted(new_state.items()):
        print(f"  {theme:34} {st.status:10} {st.evidence_count_recent_90d:>8}  {st.last_evidence_date:>12}")


if __name__ == "__main__":
    main()
