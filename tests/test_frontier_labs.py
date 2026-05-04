from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from src.collectors import frontier_labs
from src.collectors.frontier_labs import FrontierItem, FrontierLab
from src.collectors.stocks import StockSignal
from src.config import HOLDINGS
from src.processors.frontier_labs_filter import (
    FrontierKeyPoint,
    _parse_output,
    select_frontier_items,
)
from src.renderer.render import render_email

_NOW = datetime(2026, 5, 4, 12, 0, tzinfo=UTC)


def _item(
    title: str,
    *,
    lab: str = "OpenAI",
    url: str = "https://example.com/news",
    source: str = "Reuters",
    source_type: frontier_labs.SourceType = "google_news",
    published_at: datetime | None = None,
) -> FrontierItem:
    return FrontierItem(
        lab=lab,
        title=title,
        snippet="summary",
        published_at=published_at or (_NOW - timedelta(hours=1)),
        url=url,
        source=source,
        source_type=source_type,
        related_tickers=["MSFT", "NVDA", "TSM"],
    )


def _one_signal() -> StockSignal:
    return StockSignal(
        holding=HOLDINGS[0],
        last_close=None,
        sma_120=None,
        sma_200=None,
        delta_120=None,
        delta_200=None,
        signal="NONE",
        error="test_fixture",
    )


def test_frontier_labs_collector_dedupes_and_delays_state_commit(tmp_path, monkeypatch) -> None:
    lab = FrontierLab(
        name="OpenAI",
        queries=['"OpenAI"'],
        official_feeds=["https://openai.com/news/rss.xml"],
        related_tickers=["MSFT", "NVDA", "TSM"],
    )
    official = _item(
        "OpenAI announces data center partnership",
        source="OpenAI",
        source_type="official",
        url="https://openai.com/news/data-center",
    )
    duplicate = _item(
        "OpenAI announces data center partnership - Reuters",
        source="Reuters",
        url="https://reuters.com/openai-data-center",
    )
    already_pushed = _item(
        "OpenAI funding rumor",
        url="https://example.com/already",
    )
    state_path = tmp_path / "pushed_frontier_labs.json"
    state_path.write_text(
        json.dumps({
            frontier_labs._content_hash(lab.name, already_pushed): (
                _NOW - timedelta(days=1)
            ).isoformat()
        }),
        encoding="utf-8",
    )
    original_state = state_path.read_text(encoding="utf-8")

    monkeypatch.setattr(frontier_labs, "FRONTIER_LABS", [lab])
    monkeypatch.setattr(
        frontier_labs,
        "last_24h_window",
        lambda: (_NOW - timedelta(hours=24), _NOW),
    )
    monkeypatch.setattr(frontier_labs, "_fetch_official_feed", lambda *_: [official])
    monkeypatch.setattr(frontier_labs, "_fetch_google_news", lambda *_: [duplicate, already_pushed])

    bundles, pending = frontier_labs.fetch_all(state_path)

    assert len(bundles) == 1
    assert bundles[0].items == [official]
    assert state_path.read_text(encoding="utf-8") == original_state

    new_hash = frontier_labs._content_hash(lab.name, official)
    assert new_hash in pending
    frontier_labs.commit_pushed(state_path, pending)
    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert new_hash in saved


def test_frontier_labs_collector_keeps_google_when_official_fails(tmp_path, monkeypatch) -> None:
    lab = FrontierLab(
        name="OpenAI",
        queries=['"OpenAI"'],
        official_feeds=["https://openai.com/news/rss.xml"],
        related_tickers=["MSFT"],
    )
    google_item = _item("OpenAI signs compute deal", url="https://example.com/compute")

    monkeypatch.setattr(frontier_labs, "FRONTIER_LABS", [lab])
    monkeypatch.setattr(
        frontier_labs,
        "last_24h_window",
        lambda: (_NOW - timedelta(hours=24), _NOW),
    )
    monkeypatch.setattr(
        frontier_labs,
        "_fetch_official_feed",
        lambda *_: (_ for _ in ()).throw(RuntimeError("rss down")),
    )
    monkeypatch.setattr(frontier_labs, "_fetch_google_news", lambda *_: [google_item])

    bundles, pending = frontier_labs.fetch_all(tmp_path / "pushed.json")

    assert bundles[0].items == [google_item]
    assert len(bundles[0].errors) == 1
    assert frontier_labs._content_hash(lab.name, google_item) in pending


def test_frontier_labs_collector_caps_candidates_per_lab(tmp_path, monkeypatch) -> None:
    lab = FrontierLab(name="Anthropic", queries=['"Anthropic"'], official_feeds=[], related_tickers=["GOOG"])
    items = [
        _item(
            f"Anthropic enterprise deal {i}",
            lab="Anthropic",
            url=f"https://example.com/{i}",
            published_at=_NOW - timedelta(minutes=i),
        )
        for i in range(9)
    ]

    monkeypatch.setattr(frontier_labs, "FRONTIER_LABS", [lab])
    monkeypatch.setattr(
        frontier_labs,
        "last_24h_window",
        lambda: (_NOW - timedelta(hours=24), _NOW),
    )
    monkeypatch.setattr(frontier_labs, "_fetch_google_news", lambda *_: items)

    bundles, pending = frontier_labs.fetch_all(tmp_path / "pushed.json", max_items_per_lab=8)

    assert len(bundles[0].items) == 8
    assert len(pending) == 8


def test_frontier_labs_malformed_state_degrades_to_empty(tmp_path, monkeypatch) -> None:
    lab = FrontierLab(name="Anthropic", queries=['"Anthropic"'], official_feeds=[], related_tickers=["GOOG"])
    item = _item("Anthropic signs enterprise deal", lab="Anthropic", url="https://example.com/deal")
    state_path = tmp_path / "pushed_frontier_labs.json"

    monkeypatch.setattr(frontier_labs, "FRONTIER_LABS", [lab])
    monkeypatch.setattr(
        frontier_labs,
        "last_24h_window",
        lambda: (_NOW - timedelta(hours=24), _NOW),
    )
    monkeypatch.setattr(frontier_labs, "_fetch_google_news", lambda *_: [item])

    state_path.write_text("[]", encoding="utf-8")
    bundles, pending = frontier_labs.fetch_all(state_path)
    assert bundles[0].items == [item]
    assert frontier_labs._content_hash(lab.name, item) in pending

    state_path.write_text(json.dumps({"bad_hash": ["not", "an", "iso", "date"]}), encoding="utf-8")
    bundles, pending = frontier_labs.fetch_all(state_path)
    assert bundles[0].items == [item]
    assert frontier_labs._content_hash(lab.name, item) in pending


def test_frontier_labs_parse_keeps_major_item_with_tickers() -> None:
    items = [
        _item("OpenAI announces data center partnership", url="https://openai.com/news/x", source="OpenAI", source_type="official"),
        _item("Reuters coverage", url="https://reuters.com/x"),
    ]

    out = _parse_output(
        "▦ 1,2: yes | score=5 | tickers=MSFT,NVDA,TSM | 新增数据中心合作,若落地将继续支撑云与 GPU 需求",
        items,
        "OpenAI",
    )

    assert len(out) == 1
    assert out[0].lab == "OpenAI"
    assert out[0].score == 5
    assert out[0].related_tickers == ["MSFT", "NVDA", "TSM"]
    assert out[0].source_url == "https://openai.com/news/x"


def test_frontier_labs_parse_accepts_common_ticker_aliases_and_separators() -> None:
    items = [_item("Anthropic expands cloud partnership", lab="Anthropic", url="https://example.com/x")]

    out = _parse_output(
        "▦ 1: yes | score=4 | tickers=GOOGL、NVDA/MSFT | 企业采用提速,继续支撑云与 GPU 需求",
        items,
        "Anthropic",
    )

    assert len(out) == 1
    assert out[0].related_tickers == ["GOOG", "NVDA", "MSFT"]


def test_frontier_labs_parse_drops_low_score_missing_tickers_and_unsafe_url() -> None:
    items = [
        _item("safe", url="https://example.com/safe"),
        _item("unsafe", url="javascript:alert(1)"),
    ]

    out = _parse_output(
        "\n".join([
            "▦ 1: yes | score=3 | tickers=MSFT | 普通产品更新",
            "▦ 1: yes | score=4 | 没有 tickers 字段",
            "▦ 1: yes | score=4 | tickers=AMZN | 只影响非持仓链",
            "▦ 2: yes | score=5 | tickers=MSFT | URL 不安全",
        ]),
        items,
        "OpenAI",
    )

    assert out == []


def test_select_frontier_items_limits_total_and_one_per_lab() -> None:
    old = _NOW - timedelta(hours=3)
    newer = _NOW - timedelta(hours=1)
    points = [
        FrontierKeyPoint(
            lab="OpenAI",
            text="低分同实验室",
            related_tickers=["MSFT"],
            source_url="https://a",
            source_name="Reuters",
            score=4,
            published_at=newer,
        ),
        FrontierKeyPoint(
            lab="OpenAI",
            text="高分同实验室",
            related_tickers=["MSFT"],
            source_url="https://b",
            source_name="Reuters",
            score=5,
            published_at=old,
        ),
        FrontierKeyPoint(
            lab="Anthropic",
            text="另一实验室",
            related_tickers=["GOOG"],
            source_url="https://c",
            source_name="Bloomberg",
            score=4,
            published_at=newer,
        ),
    ]

    out = select_frontier_items(points)

    assert len(out) == 2
    assert [p.lab for p in out] == ["OpenAI", "Anthropic"]
    assert out[0].text == "高分同实验室"


def test_render_frontier_labs_under_yesterday_without_empty_state() -> None:
    point = FrontierKeyPoint(
        lab="OpenAI",
        text="新增数据中心合作,若落地将继续支撑云与 GPU 需求",
        related_tickers=["MSFT", "NVDA"],
        source_url="https://openai.com/news/x",
        source_name="OpenAI",
        score=5,
    )

    html = render_email(
        signals=[_one_signal()],
        generated_at=_NOW,
        company_news=[],
        frontier_labs_items=[point],
    )

    assert "昨日动态" in html
    assert "前沿模型" in html
    assert "Frontier Labs" in html
    assert "OpenAI" in html
    assert "新增数据中心合作" in html
    assert 'href="https://openai.com/news/x"' in html

    empty_html = render_email(
        signals=[_one_signal()],
        generated_at=_NOW,
        company_news=[],
        frontier_labs_items=[],
    )
    assert "前沿模型" not in empty_html


def test_openai_and_anthropic_are_not_holdings() -> None:
    tickers = {h.ticker for h in HOLDINGS}
    assert "OpenAI" not in tickers
    assert "Anthropic" not in tickers
