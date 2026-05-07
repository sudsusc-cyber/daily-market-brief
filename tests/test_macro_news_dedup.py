"""测试 macro_news 跨天 + 同日双层去重。

不调用真实 RSS 源。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from src.collectors import macro_news
from src.collectors.macro_news import MacroFeedBundle, MacroNewsItem

_NOW = datetime(2026, 5, 4, 12, 0, tzinfo=UTC)


def _item(title: str, *, source: str = "WSJ", published_at: datetime | None = None) -> MacroNewsItem:
    return MacroNewsItem(
        title=title,
        published_at=published_at or (_NOW - timedelta(hours=3)),
        url=f"https://example.com/{hash(title) & 0xFFFF:x}",
        source=source,
    )


def _bundle(source: str, items: list[MacroNewsItem]) -> MacroFeedBundle:
    return MacroFeedBundle(source=source, items=items)


# ─── _content_hash ─────────────────────────────────────────────


def test_content_hash_normalizes_media_suffix() -> None:
    a = _item("Fed raises rates by 25bp - WSJ")
    b = _item("Fed raises rates by 25bp - Reuters")
    assert macro_news._content_hash("WSJ", a) == macro_news._content_hash("WSJ", b)


def test_content_hash_different_source_different_hash() -> None:
    a = _item("Fed raises rates", source="WSJ")
    h_wsj = macro_news._content_hash("WSJ", a)
    h_ft = macro_news._content_hash("FT", a)
    assert h_wsj != h_ft


# ─── _similar / fuzzy dedup ────────────────────────────────────


def test_similar_merges_near_identical() -> None:
    assert macro_news._similar(
        "Fed raises interest rates by 25 basis points",
        "Fed raises interest rates by 25 basis points today",
    )


def test_similar_keeps_different_events_apart() -> None:
    assert not macro_news._similar(
        "Fed raises rates by 25 basis points",
        "Apple unveils iPhone 17 at Cupertino event",
    )


# ─── fetch_all 跨 source 模糊去重 ─────────────────────────────


def test_fetch_all_cross_source_fuzzy_dedup(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        macro_news,
        "last_24h_window",
        lambda: (_NOW - timedelta(hours=24), _NOW),
    )
    monkeypatch.setattr(macro_news, "_fetch_feed", lambda url: [
        MacroNewsItem(
            title=t,
            published_at=_NOW - timedelta(hours=1),
            url=f"https://example.com/{i}",
            source="",
        )
        for i, t in enumerate([
            "Fed raises interest rates by 25 basis points",
            "Fed raises interest rates by 25 bps",
            "Fed raises interest rates by 25 bp",
        ])
    ])

    state_path = tmp_path / "pushed_macro_news.json"
    bundles, pending = macro_news.fetch_all(state_path=state_path)

    total_items = sum(len(b.items) for b in bundles if not b.error)
    assert total_items == 1  # 跨 source 模糊去重应只保留一条


def test_fetch_all_skips_pushed_items(tmp_path, monkeypatch) -> None:
    pushed_item = _item("ECB holds rates steady", source="WSJ")
    pushed_hash = macro_news._content_hash("WSJ", pushed_item)
    state_path = tmp_path / "pushed_macro_news.json"
    state_path.write_text(
        json.dumps({pushed_hash: (_NOW - timedelta(days=1)).isoformat()}),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        macro_news,
        "last_24h_window",
        lambda: (_NOW - timedelta(hours=24), _NOW),
    )
    monkeypatch.setattr(macro_news, "_fetch_feed", lambda url: [
        MacroNewsItem(
            title="ECB holds rates steady",
            published_at=_NOW - timedelta(hours=2),
            url="https://example.com/ecb",
            source="",
        ),
    ])

    bundles, pending = macro_news.fetch_all(state_path=state_path)
    wsj = next(b for b in bundles if b.source == "WSJ" and not b.error)
    assert wsj.items == []


def test_fetch_all_does_not_write_state_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        macro_news,
        "last_24h_window",
        lambda: (_NOW - timedelta(hours=24), _NOW),
    )
    monkeypatch.setattr(macro_news, "_fetch_feed", lambda url: [
        MacroNewsItem(
            title="Breaking macro news",
            published_at=_NOW - timedelta(hours=1),
            url="https://example.com/x",
            source="",
        ),
    ])

    state_path = tmp_path / "pushed_macro_news.json"
    macro_news.fetch_all(state_path=state_path)
    assert not state_path.exists()


# ─── commit_pushed ──────────────────────────────────────────────


def test_commit_pushed_writes_state_file(tmp_path) -> None:
    state_path = tmp_path / "pushed_macro_news.json"
    pending = {"abc123def4567890": _NOW.isoformat()}
    macro_news.commit_pushed(state_path, pending)

    assert state_path.exists()
    loaded = json.loads(state_path.read_text(encoding="utf-8"))
    assert "abc123def4567890" in loaded


# ─── purge ──────────────────────────────────────────────────────


def test_purge_expired_drops_old_entries() -> None:
    old = (_NOW - timedelta(days=8)).isoformat()
    recent = (_NOW - timedelta(days=3)).isoformat()
    pushed = {"aaa": old, "bbb": recent}
    result = macro_news._purge_expired_macro(pushed, _NOW, days=7)
    assert "bbb" in result
    assert "aaa" not in result


# ─── malformed state ────────────────────────────────────────────


def test_malformed_state_degrades_to_empty(tmp_path, monkeypatch) -> None:
    state_path = tmp_path / "pushed_macro_news.json"
    state_path.write_text("not valid json", encoding="utf-8")

    monkeypatch.setattr(
        macro_news,
        "last_24h_window",
        lambda: (_NOW - timedelta(hours=24), _NOW),
    )
    monkeypatch.setattr(macro_news, "_fetch_feed", lambda url: [
        MacroNewsItem(
            title="News",
            published_at=_NOW - timedelta(hours=1),
            url="https://example.com/x",
            source="",
        ),
    ])

    bundles, _ = macro_news.fetch_all(state_path=state_path)
    assert len(bundles) == 4


# ─── source priority for fuzzy dedup ────────────────────────────


def test_fetch_all_fuzzy_dedup_prefers_higher_priority_source(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        macro_news,
        "last_24h_window",
        lambda: (_NOW - timedelta(hours=24), _NOW),
    )
    monkeypatch.setattr(macro_news, "_fetch_feed", lambda url: [
        MacroNewsItem(
            title="Major central bank policy shift — common event",
            published_at=_NOW - timedelta(hours=1),
            url=f"https://example.com/{url[:10]}",
            source="",
        ),
    ])

    state_path = tmp_path / "pushed_macro_news.json"
    bundles, _ = macro_news.fetch_all(state_path=state_path)

    total = sum(len(b.items) for b in bundles if not b.error)
    assert total == 1
    # Bloomberg 优先级最高(1),应被保留
    bl = next(b for b in bundles if b.source == "Bloomberg" and not b.error)
    assert len(bl.items) == 1
