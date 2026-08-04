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


def test_macro_quality_fallback_never_renders_raw_rss_titles() -> None:
    """宏观加工失败时只展示受控占位语，不恢复早期原始列表。"""
    raw_title = "Opinion | Can the U.S. Treasury Save the Yen?"
    html = render_email(
        signals=[_one_signal()],
        generated_at=datetime.now(UTC),
        macro_news=[SimpleNamespace(
            source="WSJ",
            error=None,
            items=[SimpleNamespace(
                title=raw_title,
                url="https://example.com/raw",
                published_at=datetime.now(UTC),
            )],
        )],
        macro_news_summary=None,
        macro_news_fallback_note="宏观信息整理未完成，本期从略。",
    )

    assert "宏观视野" in html
    assert "宏观信息整理未完成" in html
    assert raw_title not in html
    assert ">WSJ<" not in html


def test_render_email_decorative_logo_has_empty_alt() -> None:
    """相邻已有 ticker 文本，装饰性 logo 使用空 alt 避免屏幕阅读器重复朗读。"""
    s = _one_signal()
    html = render_email(
        signals=[s],
        generated_at=datetime.now(UTC),
        logo_cids={s.holding.ticker: "test_cid"},
    )
    assert 'alt=""' in html


def test_render_email_header_keeps_full_two_to_one_frame() -> None:
    """刊头图保持完整 2:1 内容，并使用流式宽度避免撑开移动端。"""
    html = render_email(
        signals=[_one_signal()],
        generated_at=datetime.now(UTC),
        header_image_url="cid:header_image",
    )
    assert '<img src="cid:header_image"\n                 width="100%"' in html
    assert 'height="320"' not in html
    assert 'height="200"' not in html
    assert "width:100%; max-width:640px; height:auto" in html


def test_render_email_mobile_layout_never_forces_desktop_canvas() -> None:
    """窄屏 QQ 邮箱不得因持仓表硬宽度而把整封邮件缩成桌面比例。"""
    html = render_email(
        signals=[_one_signal()],
        generated_at=datetime.now(UTC),
    )

    assert 'class="email-shell"' in html
    assert 'class="email-container" role="presentation" width="100%"' in html
    assert 'class="email-container" role="presentation" width="640"' not in html
    assert "width:100%; max-width:640px" in html
    # 640px 只允许出现在 Outlook 专用条件注释中，普通 QQ/iOS/Android
    # 客户端不能把它当作内容的硬最小宽度。
    fixed_width_table = html.index('<table role="presentation" width="640"')
    mso_open = html.rfind("<!--[if mso]>", 0, fixed_width_table)
    mso_close = html.index("<![endif]-->", fixed_width_table)
    assert mso_open < fixed_width_table < mso_close
    assert html.count('width="640"') == 1
    assert 'class="holdings-table" width="100%"' in html
    assert "width:100%; max-width:590px; table-layout:fixed" in html
    assert '<table width="590"' not in html
    assert "@media only screen and (max-width:520px)" in html
    assert ".email-shell { padding-left:6px !important; padding-right:6px !important; }" in html
    assert '.holding-name { display:block !important;' in html
    assert ".holding-name { display:none" not in html


def test_render_email_dynamic_source_links_can_wrap_on_narrow_screens() -> None:
    """动态来源名再长也不能把 QQ 邮箱整封邮件撑成桌面画布。"""
    long_source = "ExtremelyLongUnbrokenDynamicSourceName" * 8
    html = render_email(
        signals=[_one_signal()],
        generated_at=datetime.now(UTC),
        company_news=[SimpleNamespace()],  # 进入昨日动态区块
        company_news_summary=SimpleNamespace(
            summary_html="<div>摘要</div>",
            footnotes=[SimpleNamespace(index=1, url="https://example.com", source=long_source)],
        ),
    )

    assert long_source in html
    assert 'class="source-link"' in html
    assert "white-space:normal; overflow-wrap:anywhere; word-break:break-word" in html
    assert ".source-link { white-space:normal !important;" in html
    assert f"white-space:nowrap;\">[1] {long_source}" not in html


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
            "coverage": {
                "valid_metrics": 5,
                "total_metrics": 5,
                "stale_metrics": 1,
            },
        },
    )

    assert 'data-sentiment-gauge="true"' in html
    assert ">67.5<" in html
    assert "今日情绪" not in html
    assert "background-color:#A96D4F" in html
    assert "background-color:#D97757" not in html
    assert "color:#FFFFFF" in html
    assert 'data-sentiment-score-track="true"' in html
    assert 'data-sentiment-score-position="14"' in html
    assert 'data-sentiment-pointer-position="14"' in html
    assert 'data-sentiment-score-bubble="true"' in html
    assert 'data-sentiment-score-face="continuous-corner"' in html
    assert "-webkit-border-radius:12px; border-radius:12px" in html
    assert 'data-sentiment-score-tail-track="true"' in html
    assert 'display:inline-block; vertical-align:bottom' in html
    assert 'data-sentiment-score-tail="true"' in html
    assert "&#9660;" in html
    assert 'color:#A96D4F;"><span data-sentiment-score-tail="true">' in html
    assert 'data-sentiment-label="true"' not in html
    assert "极度恐慌" in html
    assert "极度贪婪" in html
    assert 'data-sentiment-glass-tube="true"' in html
    assert 'class="sentiment-glass-tube"' in html
    assert 'data-sentiment-glass-style="quiet-capsule"' in html
    assert 'data-sentiment-glass-tip="curved-clear"' in html
    assert 'data-sentiment-glass-rim="true"' in html
    assert 'data-sentiment-color-bar="true"' in html
    assert html.count('data-sentiment-glass-cell="true"') == 20
    assert "background-image:radial-gradient" not in html
    assert "border:1px solid #C8C1B5" in html
    assert "border-radius:999px" in html
    assert ".sentiment-glass-tube { border-radius:999px 0 0 999px !important; }" in html
    assert "width:100%; max-width:100%; table-layout:fixed" in html
    assert "clip-path:polygon(0 0,calc(100% - 12px) 0,calc(100% - 6px) 12%" in html
    assert "padding:1px 4px 1px 1px" in html
    assert "background-color:rgba(248,245,238,0.42)" in html
    assert "0 2px 8px rgba(26,26,26,0.10)" in html
    assert "background-color:#4F6870" in html  # 渐变不支持时仍保留纯色色带
    assert "数据覆盖" not in html
    assert "项沿用缓存" not in html
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
    assert len(neutral["pointer_cells"]) == 21
    assert neutral["pointer_cells"][10]["active"] is True
    assert sum(cell["active"] for cell in neutral["pointer_cells"]) == 1
    assert neutral["bubble_layout"] == {
        "left_width": 40.0,
        "region_width": 20.0,
        "right_width": 40.0,
        "align": "center",
    }
    extreme_fear = _build_sentiment_gauge({"score": 0.0, "verdict": "极度恐慌"})
    extreme_greed = _build_sentiment_gauge({"score": 100.0, "verdict": "极度贪婪"})
    assert extreme_fear is not None
    assert extreme_greed is not None
    assert extreme_fear["pointer_index"] == 0
    assert extreme_fear["bubble_layout"]["left_width"] == 0.0
    assert extreme_fear["bubble_layout"]["align"] == "left"
    assert extreme_greed["pointer_index"] == 20
    assert extreme_greed["bubble_layout"]["right_width"] == 0.0
    assert extreme_greed["bubble_layout"]["align"] == "right"


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


def test_sentiment_gauge_label_is_derived_from_score() -> None:
    """即使上游旧 label 漂移，文字与徽章颜色也必须由同一分数档位决定。"""
    gauge = _build_sentiment_gauge({"score": 67.5, "verdict": "今日情绪 · 中性"})

    assert gauge is not None
    assert gauge["label"] == "偏热"
    assert gauge["active_color"] == "#A96D4F"


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
