"""
完整模拟邮件渲染，包含长期判断区块。
纯本地、零网络、零 LLM。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date, timedelta, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.processors.thesis.models import ThesisEvidence, ThesisState
from src.processors.thesis.rules import run_state_transitions
from src.processors.thesis.renderer import build_judgment_section


@dataclass
class JudgmentSection:
    """Jinja2 用 .items 访问属性，不能直接用 dict（会和 .items() 方法冲突）。"""
    items: list[dict[str, Any]]


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
                tickers=tickers, eid=f"ev-{theme}-{today_str}")
        )

    # ── 跑状态机 ──
    holdings_tickers = ["MSFT", "NVDA", "BRK.B", "KO", "COST",
                        "AAPL", "GOOG", "MCO", "AXP", "TSM"]
    new_state, events = run_state_transitions(
        today=today, state=state, recent_evidence=evidence_today,
        holdings_tickers=holdings_tickers,
    )

    raw_section = build_judgment_section(events)

    # 将 dict 转为 Jinja2 兼容的 dataclass（避免 .items 属性 vs 方法冲突）
    if raw_section:
        section = JudgmentSection(items=raw_section["items"])
    else:
        section = None

    # ── 渲染为真实 email HTML ──
    from src.renderer.render import _build_env
    env = _build_env()
    template = env.get_template("email.html.j2")

    from src.collectors.stocks import StockSignal
    from src.config import HOLDINGS

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
            SentimentMetric(name="HSI RSI(14)", current=48.0, prior=47.5, rating=None),
        ],
        fetched_at=datetime.now(ZoneInfo("UTC")),
    )

    from src.processors.news_summarizer import CompanyNewsSummary, Footnote
    mock_cn_summary = CompanyNewsSummary(
        summary_html=(
            '<div style="margin:0 0 10px 0;'
            "font-family:'Noto Serif SC','Source Han Serif SC',Charter,Georgia,serif;"
            'font-size:16px;line-height:1.9;color:#1A1A1A;">'
            '<span style="color:#7A1F2B;">英伟达</span>'
            '<span style="color:#D9D2BE;margin:0 6px;">|</span>'
            "Blackwell GPU 量产进度超预期，资本开支指引上调。</div>"
        ),
        footnotes=[Footnote(index=1, url="https://example.com/1", source="Reuters")],
    )

    html = template.render(
        signals=mock_signals,
        generated_at=datetime.now(ZoneInfo("Asia/Shanghai")),
        logo_cids={},
        header_image_url="https://images.pexels.com/photos/691668/pexels-photo-691668.jpeg"
                         "?auto=compress&cs=tinysrgb&w=1280&h=400&fit=crop",
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
        frontier_labs_items=None,
        judgment_section=section,
    )

    out = Path("/tmp/email_preview_with_judgment.html")
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
            print(f"  [{i}] {item['label']} ｜ {item['text']}")
    else:
        print("  (无渐明事件 — 区块不渲染)")
    print("═" * 66)

    # 状态一览
    print()
    print(f"{'主题':36} {'状态':10} {'90d证据':>8}  {'最近证据':>12}")
    print("-" * 70)
    for theme, st in sorted(new_state.items()):
        print(f"  {theme:34} {st.status:10} {st.evidence_count_recent_90d:>8}  {st.last_evidence_date:>12}")


if __name__ == "__main__":
    main()
