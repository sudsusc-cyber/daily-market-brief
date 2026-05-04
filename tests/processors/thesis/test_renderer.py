"""test_renderer.py — judgment_section 构建单测"""

from src.processors.thesis.models import ThesisEvent
from src.processors.thesis.renderer import build_judgment_section


def make_event(theme="test-theme", headline="测试标题", tickers=None, url=None):
    return ThesisEvent(
        kind="substantiate",
        theme=theme,
        related_tickers=tickers or ["TEST"],
        headline=headline,
        source_url=url,
        source_section="company_news",
    )


def test_empty_returns_none():
    assert build_judgment_section([]) is None


def test_single_event():
    e = make_event(headline="MSFT AI capex 获得新证据支持")
    result = build_judgment_section([e])
    assert result is not None
    assert len(result["items"]) == 1
    assert result["items"][0]["label"] == "渐明"
    assert result["items"][0]["text"] == "MSFT AI capex 获得新证据支持"


def test_two_events():
    events = [
        make_event(theme="t1", headline="第一条"),
        make_event(theme="t2", headline="第二条"),
    ]
    result = build_judgment_section(events)
    assert result is not None
    assert len(result["items"]) == 2


def test_truncate_to_three():
    events = [
        make_event(theme=f"t{i}", headline=f"第{i}条")
        for i in range(1, 6)
    ]
    result = build_judgment_section(events)
    assert result is not None
    assert len(result["items"]) == 3
    assert result["items"][0]["text"] == "第1条"
    assert result["items"][2]["text"] == "第3条"


def test_url_passthrough():
    e = make_event(url="https://example.com/news")
    result = build_judgment_section([e])
    assert result["items"][0]["url"] == "https://example.com/news"


def test_none_url():
    e = make_event(url=None)
    result = build_judgment_section([e])
    assert result["items"][0]["url"] is None
