"""端到端 render_email() 测试 — 覆盖空数据 / silence note / 最小输入。

补足 test_render_filters.py 的 gap:filter 单测齐全,但整体渲染路径
(模板分支、降级、空集合处理)缺测试。Round 1 死代码审计指出的盲点。
"""
from __future__ import annotations

from datetime import UTC, datetime

from src.collectors.figures import FigureBundle
from src.collectors.stocks import StockSignal
from src.config import HOLDINGS
from src.renderer.render import render_email


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
