from __future__ import annotations

from src.collectors.company_news import _load_pushed_news
from src.collectors.figures import _load_pushed
from src.collectors.macro_news import _load_pushed_macro
from src.processors.subject import generator


def test_dedupe_loaders_reject_wrong_root_type(tmp_path) -> None:
    path = tmp_path / "state.json"
    path.write_text("[]", encoding="utf-8")

    assert _load_pushed_news(path) == {}
    assert _load_pushed_macro(path) == {}
    assert _load_pushed(path) == {}


def test_dedupe_loaders_drop_non_string_values(tmp_path) -> None:
    path = tmp_path / "state.json"
    path.write_text('{"ok":"2026-07-11T00:00:00+00:00","bad":null}', encoding="utf-8")

    assert _load_pushed_news(path) == {"ok": "2026-07-11T00:00:00+00:00"}
    assert _load_pushed_macro(path) == {"ok": "2026-07-11T00:00:00+00:00"}
    assert _load_pushed(path) == {"ok": "2026-07-11T00:00:00+00:00"}


def test_subject_cache_wrong_root_type_degrades_to_empty(tmp_path, monkeypatch) -> None:
    path = tmp_path / "subject_cache.json"
    path.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(generator, "CACHE_PATH", path)

    assert generator._load_cache() == {}
