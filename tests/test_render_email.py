"""端到端 render_email() 测试 — 覆盖空数据 / silence note / 最小输入。

补足 test_render_filters.py 的 gap:filter 单测齐全,但整体渲染路径
(模板分支、降级、空集合处理)缺测试。Round 1 死代码审计指出的盲点。
"""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from src.collectors.buffett_13f import BuffettBundle, Filing13F
from src.collectors.figures import FigureBundle
from src.collectors.stocks import StockSignal
from src.config import HOLDINGS
from src.processors.thesis.renderer import JudgmentSection
from src.renderer.render import _build_sentiment_gauge, render_email


def _one_figure_bundle() -> FigureBundle:
    """非空 figures 进入"关键发言"段(模板入口条件)。items 留空模拟 LLM 全砍后场景。"""
    return FigureBundle(person="黄仁勋", query="Jensen Huang", person_en="Jensen Huang", items=[])


def _one_signal() -> StockSignal:
    """最简单的非空 signal — 1 个 holding,无价格(模拟 stocks.fetch_all 全失败)。"""
    return StockSignal(
        holding=HOLDINGS[0],
        last_close=None, sma_120=None, sma_200=None,
        delta_120=None, delta_200=None,
        signal="NONE", error="test_fixture",
    )


def test_render_email_with_minimum_data_does_not_raise() -> None:
    """所有 optional 字段 None — 不抛异常,返回非空 HTML。"""
    html = render_email(
        signals=[_one_signal()],
        generated_at=datetime.now(UTC),
    )
    assert "<html" in html.lower()
    assert len(html.encode("utf-8")) > 1000  # 至少有完整模板骨架


def test_render_email_with_silence_note_renders_it() -> None:
    """figure_summaries 全空时,figure_silence_note 应出现在 HTML 里(章节占位)。

    模板的"关键发言"段进入条件是 figures 非空(原始数据存在),所以测试也要传 figures。
    """
    note = "今夜星河无言"
    html = render_email(
        signals=[_one_signal()],
        generated_at=datetime.now(UTC),
        figures=[_one_figure_bundle()],
        figure_summaries=[],
        figure_silence_note=note,
    )
    assert note in html


def test_render_email_no_silence_note_uses_template_fallback() -> None:
    """figure_summaries 全空且 figure_silence_note=None → 模板兜底 '群贤皆默,市自为声'。"""
    html = render_email(
        signals=[_one_signal()],
        generated_at=datetime.now(UTC),
        figures=[_one_figure_bundle()],
        figure_summaries=[],
        figure_silence_note=None,
    )
    # 模板里写死的兜底字符串(email.html.j2 figure_silence_note or "...")
    assert "群贤皆默" in html


def test_render_email_logo_alt_uses_ticker_when_logo_present() -> None:
    """logo CID 提供时,模板渲染的 <img alt> 应是 ticker(round-1 hardening)。"""
    s = _one_signal()
    html = render_email(
        signals=[s],
        generated_at=datetime.now(UTC),
        logo_cids={s.holding.ticker: "test_cid"},
    )
    assert f'alt="{s.holding.ticker}"' in html


def test_render_email_no_logo_falls_back_to_text_box() -> None:
    """logo_cids 不含该 ticker 时,渲染 ticker 前 3 字符的文本块兜底。"""
    s = _one_signal()
    html = render_email(
        signals=[s],
        generated_at=datetime.now(UTC),
        logo_cids={},  # 不传 logo
    )
    assert s.holding.ticker[:3] in html


def test_render_email_renders_daily_sentiment_gauge() -> None:
    """综合情绪有 score 时，渲染每天随分数变化的邮件原生仪表。"""
    sentiment = SimpleNamespace(metrics=[SimpleNamespace(
        name="CNN Fear & Greed", unit="", stale_from=None, error=None,
        current=67.5, prior=64.0, delta=3.5,
    )])
    html = render_email(
        signals=[_one_signal()],
        generated_at=datetime.now(UTC),
        sentiment=sentiment,
        sentiment_verdict={
            "verdict": "今日情绪 · 偏热",
            "argument": "风险偏好有所回升。",
            "score": 67.5,
        },
    )

    assert 'data-sentiment-gauge="true"' in html
    assert ">67.5<" in html
    assert "今日情绪" not in html
    assert "background-color:#A96D4F" in html
    assert "background-color:#D97757" not in html
    assert "color:#FFFFFF" in html
    assert 'data-sentiment-label="true"' in html
    assert "font-size:14px; color:#A96D4F; letter-spacing" in html
    assert "极度恐慌" in html
    assert "极度贪婪" in html
    assert html.count("▼") == 1


def test_render_email_omits_gauge_when_score_is_missing() -> None:
    """LLM 结论仍可显示；旧数据没有 score 时不渲染空仪表。"""
    sentiment = SimpleNamespace(metrics=[SimpleNamespace(
        name="VIX", unit="", stale_from=None, error=None,
        current=18.0, prior=17.0, delta=1.0,
    )])
    html = render_email(
        signals=[_one_signal()],
        generated_at=datetime.now(UTC),
        sentiment=sentiment,
        sentiment_verdict={"verdict": "今日情绪 · 中性", "argument": "方向仍待确认。"},
    )

    assert "今日情绪" not in html
    assert 'data-sentiment-gauge="true"' not in html


def test_sentiment_gauge_badge_color_follows_current_heat_band() -> None:
    """分数始终为白字，徽章底色由当天 0-100 分数所在色带决定。"""
    cases = [
        (10.0, "极度恐慌", "#4F6870"),
        (30.0, "偏冷", "#7C8E91"),
        (50.0, "中性", "#B8AD94"),
        (67.5, "偏热", "#A96D4F"),
        (90.0, "极度贪婪", "#7A1F2B"),
    ]
    for score, label, expected_color in cases:
        gauge = _build_sentiment_gauge({"score": score, "verdict": f"今日情绪 · {label}"})
        assert gauge is not None
        assert gauge["label"] == label
        assert gauge["active_color"] == expected_color

    neutral = _build_sentiment_gauge({"score": 50.0, "verdict": "今日情绪 · 中性"})
    assert neutral is not None
    assert len(neutral["pointer_cells"]) == 41
    assert neutral["pointer_cells"][20]["active"] is True
    assert sum(cell["active"] for cell in neutral["pointer_cells"]) == 1


def test_sentiment_gauge_uses_judge_thresholds_at_boundaries() -> None:
    """颜色边界必须与 sentiment_judge 的 25/40/60/75 档位完全一致。"""
    cases = [
        (20.0, "极度恐慌", "#4F6870"),
        (24.9, "极度恐慌", "#4F6870"),
        (25.0, "偏冷", "#7C8E91"),
        (39.9, "偏冷", "#7C8E91"),
        (40.0, "中性", "#B8AD94"),
        (59.9, "中性", "#B8AD94"),
        (60.0, "偏热", "#A96D4F"),
        (74.9, "偏热", "#A96D4F"),
        (75.0, "极度贪婪", "#7A1F2B"),
        (79.9, "极度贪婪", "#7A1F2B"),
    ]
    for score, label, color in cases:
        gauge = _build_sentiment_gauge({"score": score, "verdict": label})
        assert gauge is not None
        assert gauge["active_color"] == color

    gauge = _build_sentiment_gauge({"score": 50.0, "verdict": "中性"})
    assert gauge is not None
    assert [segment["width"] for segment in gauge["segments"]] == [25, 15, 20, 15, 25]


# ─── 13F 区块重定位测试 ──────────────────────────────────────────────

_MOCK_JUDGMENT = JudgmentSection(
    items=[
        {"thesis": "AI基础设施资本开支将持续十年以上", "tail": "获得新证据支持。"},
        {"thesis": "保险定价权在经济周期中持续增强", "tail": "获得新证据支持。"},
    ],
)


def _mock_13f_new() -> BuffettBundle:
    return BuffettBundle(
        latest=Filing13F(
            accession_no="0001067983-26-000005",
            filed_at=datetime(2026, 5, 1, 14, 0, tzinfo=UTC),
            title="13F-HR",
        ),
        is_new=True,
        days_since_filed=3,
    )


def _mock_13f_old() -> BuffettBundle:
    return BuffettBundle(
        latest=Filing13F(
            accession_no="0001067983-26-000005",
            filed_at=datetime(2026, 5, 1, 14, 0, tzinfo=UTC),
            title="13F-HR",
        ),
        is_new=False,
        days_since_filed=3,
    )


def test_template_renders_judgment_only() -> None:
    """judgment 有,13F 无 → 含 ❀ 和判断,不含 13F 文案。"""
    html = render_email(
        signals=[_one_signal()],
        generated_at=datetime.now(UTC),
        judgment_section=_MOCK_JUDGMENT,
    )
    assert "❀" in html
    assert "AI基础设施资本开支将持续十年以上" in html
    assert "伯克希尔本季度 13F" not in html


def test_template_renders_judgment_with_13f() -> None:
    """judgment 有,13F.is_new → 含判断 + 13F 备注 + SEC 链接。"""
    html = render_email(
        signals=[_one_signal()],
        generated_at=datetime.now(UTC),
        judgment_section=_MOCK_JUDGMENT,
        buffett_13f=_mock_13f_new(),
    )
    assert "❀" in html
    assert "AI基础设施资本开支将持续十年以上" in html
    assert "伯克希尔本季度 13F 已于 5 月 1 日披露" in html
    assert "前往 SEC EDGAR 查阅持仓" in html
    assert "0001067983" in html
    assert "margin-top:36px" in html  # 判断存在时 13F 上方 36px 间距


def test_template_renders_13f_only() -> None:
    """judgment None,13F.is_new → 含 ❀ + 13F,不含判断。"""
    html = render_email(
        signals=[_one_signal()],
        generated_at=datetime.now(UTC),
        buffett_13f=_mock_13f_new(),
    )
    assert "❀" in html
    assert "伯克希尔本季度 13F 已于 5 月 1 日披露" in html
    assert "AI基础设施" not in html
    assert "margin-top:0" in html  # 无判断时 13F 上方不额外加间距


def test_template_omits_section_when_both_empty() -> None:
    """judgment None,13F.is_new=False → 整个 tr 跳过,不含 ❀。"""
    html = render_email(
        signals=[_one_signal()],
        generated_at=datetime.now(UTC),
        buffett_13f=_mock_13f_old(),
    )
    assert "❀" not in html
    assert "伯克希尔本季度 13F" not in html


def test_template_13f_no_longer_in_figures_section() -> None:
    """旧位置:13F 不再出现在'关键发言'章节。"""
    html = render_email(
        signals=[_one_signal()],
        generated_at=datetime.now(UTC),
        figures=[_one_figure_bundle()],
        buffett_13f=_mock_13f_new(),
    )
    # 关键发言章节的 section header 应有
    assert "关键发言" in html
    # 但 Berkshire 13F 小标题不应在 figures 区域内出现
    assert "Berkshire 13F" not in html
    # 系统通知文案也被移除
    assert "SEC EDGAR 检测到" not in html
