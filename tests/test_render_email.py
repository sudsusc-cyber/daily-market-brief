"""端到端 render_email() 测试 — 覆盖空数据 / silence note / 最小输入。

补足 test_render_filters.py 的 gap:filter 单测齐全,但整体渲染路径
(模板分支、降级、空集合处理)缺测试。Round 1 死代码审计指出的盲点。
"""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from bs4 import BeautifulSoup

from src.collectors.buffett_13f import BuffettBundle, Filing13F
from src.collectors.figures import FigureBundle
from src.collectors.jiangsu_fuel import JiangsuFuelAlert
from src.collectors.stocks import StockSignal
from src.config import HOLDINGS
from src.processors.thesis.renderer import JudgmentSection
from src.renderer.render import (
    _EMAIL_HTML_BUDGET_BYTES,
    _build_sentiment_gauge,
    _compact_inline_styles,
    render_email,
)
from src.utils.email_typography import EMAIL_EDITORIAL_SERIF, EMAIL_NUMERIC_FEATURES


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


def test_inline_style_compaction_preserves_body_and_media_css() -> None:
    raw = (
        '<div style=" color : #7A1F2B ; margin : 0  4px ; ">正文 空格保留</div>'
        '<style>.x { color: red; }</style>'
    )

    compacted = _compact_inline_styles(raw)

    assert 'style="color:#7A1F2B;margin:0  4px"' in compacted
    assert "正文 空格保留" in compacted
    assert "<style>.x { color: red; }</style>" in compacted


def test_render_email_with_minimum_data_does_not_raise() -> None:
    """所有 optional 字段 None — 不抛异常,返回非空 HTML。"""
    html = render_email(
        signals=[_one_signal()],
        generated_at=datetime.now(UTC),
    )
    assert "<html" in html.lower()
    assert len(html.encode("utf-8")) > 1000  # 至少有完整模板骨架


def test_render_email_uses_one_editorial_number_system_everywhere() -> None:
    """全邮件统一齐高数字骨架，并启用等宽数字。"""
    signal = StockSignal(
        holding=HOLDINGS[0],
        last_close=1234.56,
        sma_120=1100.0,
        sma_200=900.0,
        delta_120=0.1223,
        delta_200=0.3717,
        signal="NONE",
    )
    html = render_email(
        signals=[signal],
        generated_at=datetime(2026, 8, 17, 7, 0, tzinfo=UTC),
        jiangsu_fuel_alert=_mock_jiangsu_fuel(),
    )

    assert f"font-family:{EMAIL_EDITORIAL_SERIF}" in html
    assert EMAIL_NUMERIC_FEATURES.rstrip(";") in html
    assert EMAIL_EDITORIAL_SERIF.startswith("Cambria,'Times New Roman'")
    assert not EMAIL_EDITORIAL_SERIF.startswith("Georgia")
    assert "font-family:Charter,Georgia" not in html
    assert "font-family:'Noto Serif SC','Songti SC','SimSun',Georgia" not in html
    assert "letter-spacing:0" in html  # 数值本身不再被额外拉开字距
    assert "1,234.56" in html


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


def test_company_quality_fallback_never_renders_raw_news_titles() -> None:
    """个股摘要失败时只展示受控占位语，不恢复逐公司原始列表。"""
    raw_title = "Microsoft announces an unfiltered raw update"
    html = render_email(
        signals=[_one_signal()],
        generated_at=datetime.now(UTC),
        company_news=[SimpleNamespace(
            holding=HOLDINGS[0],
            error=None,
            items=[SimpleNamespace(
                title=raw_title,
                url="https://example.com/raw",
                published_at=datetime.now(UTC),
                source="Reuters",
            )],
        )],
        company_news_summary=None,
        company_news_fallback_note="个股动态整理未完成，本期从略。",
    )

    assert "昨日动态" in html
    assert "个股动态整理未完成" in html
    assert raw_title not in html


def test_figure_processing_failure_wins_over_silence_copy() -> None:
    """人物加工失败不能再伪装成“群贤皆默”。"""
    html = render_email(
        signals=[_one_signal()],
        generated_at=datetime.now(UTC),
        figures=[_one_figure_bundle()],
        figure_summaries=[],
        figure_silence_note="群贤皆默，市自为声。",
        figure_fallback_note="关键发言整理未完成，本期从略。",
    )

    assert "关键发言整理未完成" in html
    assert "群贤皆默" not in html


def test_frontier_processing_failure_is_visible() -> None:
    html = render_email(
        signals=[_one_signal()],
        generated_at=datetime.now(UTC),
        frontier_labs_items=[],
        frontier_labs_fallback_note="前沿动态整理未完成，本期从略。",
    )

    assert "前沿动态整理未完成" in html


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
    assert "width:100%;max-width:640px;height:auto" in html


def test_render_email_mobile_layout_never_forces_desktop_canvas() -> None:
    """窄屏 QQ 邮箱不得因持仓表硬宽度而把整封邮件缩成桌面比例。"""
    html = render_email(
        signals=[_one_signal()],
        generated_at=datetime.now(UTC),
    )

    assert 'class="email-shell"' in html
    assert 'class="email-container" role="presentation" width="100%"' in html
    assert 'class="email-container" role="presentation" width="640"' not in html
    assert "width:100%;max-width:640px" in html
    # 640px 只允许出现在 Outlook 专用条件注释中，普通 QQ/iOS/Android
    # 客户端不能把它当作内容的硬最小宽度。
    fixed_width_table = html.index('<table role="presentation" width="640"')
    mso_open = html.rfind("<!--[if mso]>", 0, fixed_width_table)
    mso_close = html.index("<![endif]-->", fixed_width_table)
    assert mso_open < fixed_width_table < mso_close
    assert html.count('width="640"') == 1
    assert 'class="holdings-table" width="100%"' in html
    assert "width:100%;max-width:590px;table-layout:fixed" in html
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
    assert "white-space:normal;overflow-wrap:anywhere;word-break:break-word" in html
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
            "argument": "风险偏好有所回升。第二句不应显示。",
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
    assert "风险偏好有所回升。" in html
    assert "第二句不应显示" not in html
    assert "今日情绪" not in html
    assert "background-color:#A96D4F" in html
    assert "background-color:#D97757" not in html
    assert "color:#FFFFFF" in html
    assert 'data-sentiment-score-track="true"' in html
    assert 'data-sentiment-score-position="14"' in html
    assert 'data-sentiment-pointer-position="14"' in html
    assert 'data-sentiment-score-bubble="true"' in html
    assert 'data-sentiment-score-face="continuous-corner"' in html
    assert "-webkit-border-radius:12px;border-radius:12px" in html
    assert 'data-sentiment-score-tail-track="true"' in html
    assert 'display:inline-block;vertical-align:bottom' in html
    assert 'data-sentiment-score-tail="true"' in html
    assert "&#9660;" in html
    assert 'color:#A96D4F"><span data-sentiment-score-tail="true">' in html
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
    assert "width:100%;max-width:100%;table-layout:fixed" in html
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
        {
            "thesis": "AI基础设施资本开支将持续十年以上",
            "updated": True,
            "marker": "新证据",
        },
        {
            "thesis": "保险定价权在经济周期中持续增强",
            "updated": False,
            "marker": "",
        },
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
    assert "长 期 判 断" not in html
    assert "LONG-TERM VIEW" not in html
    assert "AI基础设施资本开支将持续十年以上" in html
    assert 'data-judgment-marker="true"' in html
    assert "&nbsp;&nbsp;新证据" in html
    assert "· 新证据" not in html
    assert "获得新证据支持" not in html
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


# ─── 江苏油价预告测试 ──────────────────────────────────────────────


def _mock_jiangsu_fuel() -> JiangsuFuelAlert:
    return JiangsuFuelAlert(
        adjustment_date=datetime(2026, 8, 14, tzinfo=UTC).date(),
        days_until=2,
        direction="下调",
        detail="92 号约 -0.18 元/升；95 号约 -0.2 元/升",
        forecast_source="第一财经",
        forecast_url="https://example.com/fuel-forecast",
        forecast_title="8月14日油价预计下调",
    )


def test_template_renders_fuel_alert_in_bottom_module() -> None:
    html = render_email(
        signals=[_one_signal()],
        generated_at=datetime.now(UTC),
        jiangsu_fuel_alert=_mock_jiangsu_fuel(),
    )

    assert "❀" in html
    assert "油价预告" in html
    assert "江苏油价预告" not in html
    assert "预计 8 月 14 日 24 时下调" in html
    assert "92 号约 -0.18 元/升" in html
    assert "预测来源" not in html
    assert "最终以" not in html
    assert "江苏省发改委公告" not in html
    assert "https://example.com/fuel-forecast" not in html


def test_template_renders_judgment_13f_and_fuel_together() -> None:
    html = render_email(
        signals=[_one_signal()],
        generated_at=datetime.now(UTC),
        judgment_section=_MOCK_JUDGMENT,
        buffett_13f=_mock_13f_new(),
        jiangsu_fuel_alert=_mock_jiangsu_fuel(),
    )

    assert "AI基础设施资本开支将持续十年以上" in html
    assert "伯克希尔本季度 13F" in html
    assert "油价预告" in html
    assert html.count("margin-top:36px") >= 2


def test_template_does_not_render_fuel_forecast_metadata() -> None:
    alert = JiangsuFuelAlert(
        adjustment_date=datetime(2026, 8, 14, tzinfo=UTC).date(),
        days_until=1,
        direction="下调",
        detail="预计下调<script>alert(1)</script>",
        forecast_source="媒体<script>",
        forecast_url="javascript:alert(1)",
        forecast_title='标题\" onmouseover=\"alert(1)',
    )
    html = render_email(
        signals=[_one_signal()],
        generated_at=datetime.now(UTC),
        jiangsu_fuel_alert=alert,
    )

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "媒体&lt;script&gt;" not in html
    assert "javascript:alert(1)" not in html
    assert "onmouseover=\"alert(1)" not in html


def test_full_editorial_email_stays_below_client_clipping_budget() -> None:
    """全模块超限压缩后仍低于 96 KiB,且所有正文角标保持蓝色可点击。"""
    signals = [
        StockSignal(
            holding=holding,
            last_close=100.0 + index,
            sma_120=95.0 + index,
            sma_200=85.0 + index,
            delta_120=0.0526,
            delta_200=0.1765,
            signal=("DCA" if index % 5 == 0 else "NONE"),
        )
        for index, holding in enumerate(HOLDINGS)
    ]
    sentiment = SimpleNamespace(metrics=[
        SimpleNamespace(
            name=name,
            unit=unit,
            stale_from=None,
            error=None,
            current=current,
            prior=current - 1.0,
            delta=1.0,
        )
        for name, unit, current in (
            ("CNN Fear & Greed", "", 68.0),
            ("VIX", "", 18.5),
            ("DXY", "", 99.2),
            ("Shiller PE", "", 36.4),
            ("高收益债利差", "bp", 288.0),
        )
    ])

    def long_url(section: str, index: int) -> str:
        return (
            f"https://example.com/{section}/{index}/"
            + "source-archive-and-verification-path-" * 3
        )

    row_style = (
        "margin:0 0 10px 0;padding:0;"
        f"font-family:{EMAIL_EDITORIAL_SERIF};"
        "font-size:16px;line-height:1.9;color:#1A1A1A;letter-spacing:0.02em"
    )
    company_rows: list[str] = []
    company_footnotes: list[SimpleNamespace] = []
    for index, holding in enumerate(HOLDINGS, start=1):
        url = long_url("company", index)
        company_rows.append(
            f'<div style="{row_style}"><span style="color:#7A1F2B">'
            f'{holding.name}</span><span style="color:#D9D2BE;margin:0 6px">│</span>'
            f'公司更新了一项与长期竞争力和资本配置相关的关键进展，尚需跟踪后续执行与财务影响。'
            f'<sup><a href="{url}" style="color:#0563C1!important;'
            f'text-decoration:none!important">'
            f'[{index}]</a></sup></div>'
        )
        company_footnotes.append(SimpleNamespace(
            index=index,
            url=url,
            source="Reuters Financial Times 来源存档",
        ))

    macro_rows: list[str] = []
    macro_footnotes: list[SimpleNamespace] = []
    for index, theme in enumerate(("美联储", "地缘政治", "通胀数据"), start=1):
        url = long_url("macro", index)
        macro_rows.append(
            '<p style="margin:0 0 14px 0;font-size:16px;line-height:1.9">'
            f'<span style="color:#7A1F2B;font-weight:600">{theme}。</span>'
            + "全球市场出现了一项需要持续跟踪的宏观变化，政策路径、风险偏好与资产定价之间的传导尚未完全结束，后续数据将决定影响的持续时间。"
            f'<sup><a href="{url}" style="color:#0563C1!important;'
            f'text-decoration:none!important">'
            f'[{index}]</a></sup></p>'
        )
        macro_footnotes.append(SimpleNamespace(
            index=index,
            url=url,
            source="Financial Times 宏观来源存档",
        ))

    figure_summaries = []
    figure_footnotes = []
    for index, person in enumerate(("巴菲特", "黄仁勋", "纳德拉"), start=1):
        url = long_url("voices", index)
        figure_summaries.append(SimpleNamespace(
            person=person,
            items=[SimpleNamespace(
                text="管理层强调长期资本配置将继续围绕可持续回报与业务护城河展开。",
                footnote_index=index,
                source_url=url,
            )],
        ))
        figure_footnotes.append(SimpleNamespace(
            index=index,
            url=url,
            source="Reuters 人物采访存档",
        ))

    frontier_items = [
        SimpleNamespace(
            lab=lab,
            text="新模型更新了推理效率与企业部署能力，可能影响云与算力需求。",
            source_url=long_url("frontier", index),
            source_name="官方发布与 Reuters 核验",
        )
        for index, lab in enumerate(("OpenAI", "Anthropic"), start=1)
    ]
    judgment = JudgmentSection(items=[
        {
            "thesis": "AI 基础设施投资周期仍由企业现金流、能源与先进制程供给共同约束",
            "updated": True,
            "marker": "新证据",
        },
        {
            "thesis": "平台企业的长期定价权取决于用户黏性与持续再投资回报",
            "updated": True,
            "marker": "新变量",
        },
        {
            "thesis": "保险浮存金的稳定性仍是伯克希尔跨周期资本配置的核心基础",
            "updated": False,
            "marker": "",
        },
    ])

    html = render_email(
        signals=signals,
        generated_at=datetime(2026, 8, 6, 7, 0, tzinfo=UTC),
        header_image_url="cid:header_image",
        holdings_intro="价格与长期均线的距离仍需结合企业基本面与资本配置纪律一并观察。",
        sentiment=sentiment,
        sentiment_verdict={
            "verdict": "今日情绪 · 偏热",
            "argument": "风险偏好回升，但信用利差与波动率尚未同步转向。",
            "score": 68.0,
        },
        company_news=[SimpleNamespace()],
        company_news_summary=SimpleNamespace(
            summary_html="".join(company_rows),
            footnotes=company_footnotes,
        ),
        frontier_labs_items=frontier_items,
        figures=[SimpleNamespace()],
        figure_summaries=figure_summaries,
        figure_footnotes=figure_footnotes,
        macro_news=[SimpleNamespace()],
        macro_news_summary=SimpleNamespace(
            summary_html="".join(macro_rows),
            footnotes=macro_footnotes,
        ),
        judgment_section=judgment,
        buffett_13f=_mock_13f_new(),
        jiangsu_fuel_alert=_mock_jiangsu_fuel(),
    )

    assert all(section in html for section in (
        "持仓信号", "情绪温度计", "昨日动态", "关键发言", "宏观视野", "油价预告",
    ))
    assert "长 期 判 断" not in html
    assert "LONG-TERM VIEW" not in html
    assert len(html.encode("utf-8")) <= _EMAIL_HTML_BUDGET_BYTES

    # 超限时可以删掉章节底部重复来源清单,但每一处正文角标必须保住真实 href；
    # 这正是 2026-08-08 邮件曾退化为黑色不可点击 [N] 的回归边界。
    soup = BeautifulSoup(html, "html.parser")
    inline_anchors = {anchor.get("href"): anchor for anchor in soup.select("sup a[href]")}
    expected_inline_urls = {
        *(long_url("company", index) for index in range(1, len(HOLDINGS) + 1)),
        *(long_url("macro", index) for index in range(1, 4)),
        *(long_url("voices", index) for index in range(1, 4)),
        *(long_url("frontier", index) for index in range(1, 3)),
    }
    assert expected_inline_urls <= inline_anchors.keys()
    for url in expected_inline_urls:
        style = inline_anchors[url].get("style", "")
        assert "color:#0563C1!important" in style
        assert "text-decoration:none!important" in style
