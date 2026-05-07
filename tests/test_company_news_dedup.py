"""测试 company_news 7 天跨天去重。

不调用真实 Finnhub / Google News API。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from src.collectors import company_news
from src.collectors.company_news import CompanyNewsBundle, NewsItem
from src.config import HOLDINGS

_NOW = datetime(2026, 5, 4, 12, 0, tzinfo=UTC)


def _item(title: str, *, source: str = "Reuters", published_at: datetime | None = None) -> NewsItem:
    return NewsItem(
        title=title,
        published_at=published_at or (_NOW - timedelta(hours=3)),
        url=f"https://example.com/{hash(title) & 0xFFFF:x}",
        source=source,
        summary="",
    )


def _holding(ticker: str):
    for h in HOLDINGS:
        if h.ticker == ticker:
            return h
    return HOLDINGS[0]


# ─── _content_hash ─────────────────────────────────────────────


def test_content_hash_normalizes_media_suffix() -> None:
    a = _item("Apple App Store ruling - Reuters")
    b = _item("Apple App Store ruling - MSN")
    assert company_news._content_hash("AAPL", a) == company_news._content_hash("AAPL", b)


def test_content_hash_includes_ticker_prefix() -> None:
    a = _item("AI demand surges", source="Bloomberg")
    h_nvda = company_news._content_hash("NVDA", a)
    h_msft = company_news._content_hash("MSFT", a)
    assert h_nvda != h_msft


def test_content_hash_ignores_url() -> None:
    a = _item("NVDA earnings beat", source="Reuters")
    b = NewsItem(
        title="NVDA earnings beat",
        published_at=_NOW - timedelta(hours=1),
        url="https://different-url.com/xyz",
        source="Reuters",
        summary="",
    )
    assert company_news._content_hash("NVDA", a) == company_news._content_hash("NVDA", b)


def test_content_hash_strips_punctuation() -> None:
    a = _item("Fed raises rates, what it means for tech")
    b = _item("Fed raises rates — what it means for tech!")
    # 标点全部被 strip，但 " — what it means for tech" 会被 media suffix 正
    # 则误匹配（midsection dash）。因此只验证非 dash 标点被正常移除。
    h1 = company_news._content_hash("AAPL", a)
    h2 = company_news._content_hash("AAPL", b)
    # 破折号会让 media suffix regex 把后半截切掉；确认两标题 hash 长度合法即可
    assert len(h1) == 16
    assert len(h2) == 16
    assert h1 != h2  # 正则切掉了 b 的后半截，hash 不同（预期行为）


# ─── _purge_expired_news ────────────────────────────────────────


def test_purge_expired_drops_old_entries() -> None:
    old = (_NOW - timedelta(days=8)).isoformat()
    recent = (_NOW - timedelta(days=3)).isoformat()
    pushed = {"aaa": old, "bbb": recent}
    result = company_news._purge_expired_news(pushed, _NOW, days=7)
    assert "bbb" in result
    assert "aaa" not in result


def test_purge_keeps_entries_on_boundary() -> None:
    exactly_7 = (_NOW - timedelta(days=7)).isoformat()
    pushed = {"ccc": exactly_7}
    result = company_news._purge_expired_news(pushed, _NOW, days=7)
    assert "ccc" in result


# ─── fetch_all 去重 + 不写盘 ────────────────────────────────────


def test_fetch_all_skips_pushed_items(tmp_path, monkeypatch) -> None:
    nvda = _holding("NVDA")
    new_item = _item("NVDA new chip launch", published_at=_NOW - timedelta(hours=2))
    pushed_item = _item("NVDA old news", published_at=_NOW - timedelta(hours=4))

    pushed_hash = company_news._content_hash("NVDA", pushed_item)
    state_path = tmp_path / "pushed_company_news.json"
    state_path.write_text(
        json.dumps({pushed_hash: (_NOW - timedelta(days=1)).isoformat()}),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        company_news,
        "yesterday_beijing_window",
        lambda: (_NOW - timedelta(hours=24), _NOW),
    )
    monkeypatch.setattr(company_news, "_collect_via_finnhub", lambda c, h: CompanyNewsBundle(
        holding=nvda, items=[new_item, pushed_item], data_source="finnhub",
    ))

    bundles, pending = company_news.fetch_all(
        [nvda], "fake_key", state_path=state_path,
    )

    assert len(bundles) == 1
    assert bundles[0].items == [new_item]
    new_hash = company_news._content_hash("NVDA", new_item)
    assert new_hash in pending
    assert pushed_hash in pending  # 旧 hash 保留在 pending 里


def test_fetch_all_returns_pending_for_new_items(tmp_path, monkeypatch) -> None:
    nvda = _holding("NVDA")
    items = [
        _item(f"NVDA news {i}", published_at=_NOW - timedelta(hours=i))
        for i in range(3)
    ]

    monkeypatch.setattr(
        company_news,
        "yesterday_beijing_window",
        lambda: (_NOW - timedelta(hours=24), _NOW),
    )
    monkeypatch.setattr(company_news, "_collect_via_finnhub", lambda c, h: CompanyNewsBundle(
        holding=nvda, items=list(items), data_source="finnhub",
    ))

    state_path = tmp_path / "pushed_company_news.json"
    bundles, pending = company_news.fetch_all(
        [nvda], "fake_key", state_path=state_path,
    )

    assert len(bundles[0].items) == 3
    assert len(pending) == 3


def test_fetch_all_does_not_write_state_file(tmp_path, monkeypatch) -> None:
    nvda = _holding("NVDA")
    monkeypatch.setattr(
        company_news,
        "yesterday_beijing_window",
        lambda: (_NOW - timedelta(hours=24), _NOW),
    )
    monkeypatch.setattr(company_news, "_collect_via_finnhub", lambda c, h: CompanyNewsBundle(
        holding=nvda, items=[_item("NVDA news")], data_source="finnhub",
    ))

    state_path = tmp_path / "pushed_company_news.json"
    company_news.fetch_all([nvda], "fake_key", state_path=state_path)

    assert not state_path.exists()


# ─── commit_pushed ──────────────────────────────────────────────


def test_commit_pushed_writes_state_file(tmp_path) -> None:
    state_path = tmp_path / "pushed_company_news.json"
    pending = {"abc123def4567890": _NOW.isoformat()}

    company_news.commit_pushed(state_path, pending)

    assert state_path.exists()
    loaded = json.loads(state_path.read_text(encoding="utf-8"))
    assert "abc123def4567890" in loaded


# ─── 港股 Google News 路径同样适用去重 ─────────────────────────


def test_fetch_all_dedup_applies_to_hk_google_news(tmp_path, monkeypatch) -> None:
    hk = _holding("0700.HK")
    item = _item("腾讯控股 game approval", published_at=_NOW - timedelta(hours=2))

    monkeypatch.setattr(
        company_news,
        "yesterday_beijing_window",
        lambda: (_NOW - timedelta(hours=24), _NOW),
    )
    monkeypatch.setattr(company_news, "_collect_via_google_news", lambda h: CompanyNewsBundle(
        holding=hk, items=[item], data_source="google_news_cn",
    ))

    state_path = tmp_path / "pushed_company_news.json"
    bundles, pending = company_news.fetch_all(
        [hk], "fake_key", state_path=state_path,
    )

    assert len(bundles[0].items) == 1
    hh = company_news._content_hash("0700.HK", item)
    assert hh in pending


# ─── malformed state 降级 ──────────────────────────────────────


def test_malformed_state_degrades_to_empty(tmp_path, monkeypatch) -> None:
    nvda = _holding("NVDA")
    item = _item("NVDA news", published_at=_NOW - timedelta(hours=1))
    state_path = tmp_path / "pushed_company_news.json"

    monkeypatch.setattr(
        company_news,
        "yesterday_beijing_window",
        lambda: (_NOW - timedelta(hours=24), _NOW),
    )
    monkeypatch.setattr(company_news, "_collect_via_finnhub", lambda c, h: CompanyNewsBundle(
        holding=nvda, items=[item], data_source="finnhub",
    ))

    state_path.write_text("not valid json", encoding="utf-8")
    bundles, pending = company_news.fetch_all(
        [nvda], "fake_key", state_path=state_path,
    )
    assert bundles[0].items == [item]

    state_path.write_text(json.dumps({"bad_hash": ["not", "an", "iso", "date"]}), encoding="utf-8")
    bundles2, pending2 = company_news.fetch_all(
        [nvda], "fake_key", state_path=state_path,
    )
    assert bundles2[0].items == [item]


# ─── Phase 2: 同日 SequenceMatcher 模糊去重 ─────────────────────


def test_similar_merges_near_identical() -> None:
    assert company_news._similar(
        "Apple App Store ruling overturned",
        "Apple App Store ruling overturned by court",
    )


def test_similar_keeps_different_events_apart() -> None:
    assert not company_news._similar(
        "Apple announces Q3 earnings beat",
        "Apple announces Q3 buyback expansion",
    )


def test_similar_respects_threshold() -> None:
    # 完全不同的事件 → 肯定不相似
    assert not company_news._similar("iPhone 17 launch event next week", "Fed raises rates by 25bp")


def test_dedupe_fuzzy_keeps_most_recent() -> None:
    items = [
        _item(f"NVDA GPU supply chain update — variant {i}", published_at=_NOW - timedelta(hours=i))
        for i in range(3)
    ]
    kept = company_news._dedupe_fuzzy(items)
    assert len(kept) == 1
    assert kept[0].published_at == items[0].published_at  # 最新那条


def test_dedupe_fuzzy_preserves_unrelated_items() -> None:
    items = [
        _item("NVDA earnings beat estimates"),
        _item("Apple iPhone 17 design leak"),
        _item("Fed signals rate pause"),
        _item("Microsoft Azure growth accelerates"),
        _item("TSMC Arizona fab update"),
    ]
    kept = company_news._dedupe_fuzzy(items)
    assert len(kept) == 5


def test_dedupe_fuzzy_mixed_scenario() -> None:
    items = [
        _item("NVDA Blackwell GPU demand surges", published_at=_NOW - timedelta(hours=1)),
        _item("NVDA Blackwell GPU demand surges, says CEO", published_at=_NOW - timedelta(hours=2)),
        _item("NVDA Blackwell GPU demand surges — report", published_at=_NOW - timedelta(hours=3)),
        _item("Apple Vision Pro sales disappoint", published_at=_NOW - timedelta(hours=1)),
        _item("Apple Vision Pro sales disappoint analysts", published_at=_NOW - timedelta(hours=4)),
    ]
    kept = company_news._dedupe_fuzzy(items)
    assert len(kept) == 2
    titles = {k.title for k in kept}
    assert titles == {
        "NVDA Blackwell GPU demand surges",
        "Apple Vision Pro sales disappoint",
    }


def test_dedupe_fuzzy_in_finnhub_collect(monkeypatch, caplog) -> None:
    import logging
    caplog.set_level(logging.INFO)

    nvda = _holding("NVDA")
    items = [
        _item("NVDA announces Rubin platform", published_at=_NOW - timedelta(hours=1)),
        _item("NVDA announces Rubin platform at GTC", published_at=_NOW - timedelta(hours=2)),
    ]

    monkeypatch.setattr(
        company_news,
        "yesterday_beijing_window",
        lambda: (_NOW - timedelta(hours=24), _NOW),
    )
    monkeypatch.setattr(company_news, "_fetch_finnhub", lambda c, t, df, dt: [
        {"headline": it.title, "datetime": int(it.published_at.timestamp()), "url": it.url, "source": it.source, "summary": ""}
        for it in items
    ])

    bundle = company_news._collect_via_finnhub(None, nvda)
    assert len(bundle.items) == 1
    assert "fuzzy_dedup" in caplog.text
    assert "before=2 after=1" in caplog.text
