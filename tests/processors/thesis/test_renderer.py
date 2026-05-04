"""test_renderer.py — judgment_section 构建单测"""

from src.processors.thesis.models import ThesisEvent
from src.processors.thesis.renderer import build_judgment_section


def make_event(theme="test-theme", headline="「测试断言」获得新证据支持。",
               thesis="测试断言", tail="获得新证据支持。",
               tickers=None, url=None):
    return ThesisEvent(
        kind="substantiate",
        theme=theme,
        related_tickers=tickers or ["TEST"],
        headline=headline,
        thesis=thesis,
        tail=tail,
        source_url=url,
        source_section="company_news",
    )


def test_empty_returns_none():
    assert build_judgment_section([]) is None


def test_single_event():
    e = make_event()
    result = build_judgment_section([e])
    assert result is not None
    assert len(result.items) == 1
    assert result.items[0]["thesis"] == "测试断言"
    assert result.items[0]["tail"] == "获得新证据支持。"


def test_two_events():
    events = [
        make_event(theme="t1", thesis="断言1"),
        make_event(theme="t2", thesis="断言2"),
    ]
    result = build_judgment_section(events)
    assert result is not None
    assert len(result.items) == 2


def test_truncate_to_three():
    events = [
        make_event(theme=f"t{i}", thesis=f"断言{i}")
        for i in range(1, 6)
    ]
    result = build_judgment_section(events)
    assert result is not None
    assert len(result.items) == 3
    assert result.items[0]["thesis"] == "断言1"
    assert result.items[2]["thesis"] == "断言3"


def test_url_passthrough():
    e = make_event(url="https://example.com/news")
    result = build_judgment_section([e])
    assert result.items[0]["url"] == "https://example.com/news"


def test_none_url():
    e = make_event(url=None)
    result = build_judgment_section([e])
    assert result.items[0]["url"] is None


def test_fallback_from_old_headline():
    """旧 headline 格式不含 thesis/tail 字段时，正则切分兜底。"""
    e = ThesisEvent(
        kind="substantiate",
        theme="old-theme",
        related_tickers=["T"],
        headline="MSFT：「旧版断言文本」获得新证据支持。",
        source_section="company_news",
    )
    result = build_judgment_section([e])
    assert result is not None
    assert result.items[0]["thesis"] == "旧版断言文本"
    assert result.items[0]["tail"] == "获得新证据支持。"


def test_fallback_no_match_uses_whole_headline():
    """headline 无法切分时，thesis 留空，整句作为 tail。"""
    e = ThesisEvent(
        kind="substantiate",
        theme="bad",
        related_tickers=["T"],
        headline="完全无法解析的旧格式句子",
        source_section="company_news",
    )
    result = build_judgment_section([e])
    assert result is not None
    assert result.items[0]["thesis"] == ""
    assert result.items[0]["tail"] == "完全无法解析的旧格式句子"
