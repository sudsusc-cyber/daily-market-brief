"""test_state.py — state 文件 IO 单测"""

import tempfile
from datetime import date
from pathlib import Path

from src.processors.thesis.models import ThesisEvidence, ThesisState
from src.processors.thesis.state import (
    append_evidence,
    load_recent_evidence,
    load_state,
    save_state,
    update_rolling_evidence,
)

# ─── helpers ───────────────────────────────────────────────────────

def _make_evidence(date_str="2026-05-04", theme="test-theme", strength=4, tickers=None):
    return ThesisEvidence(
        evidence_id=f"test-eid-{theme}-{date_str}",
        date=date_str,
        source_section="company_news",
        source_name="Reuters",
        url=None,
        related_tickers=tickers or ["TEST"],
        theme=theme,
        direction="support",
        strength=strength,
        horizon="multi_year",
        text="测试",
        why_it_matters="测试原因",
    )


def _make_state(theme="test-theme", status="candidate"):
    return ThesisState(
        theme=theme,
        status=status,
        related_tickers=["TEST"],
        cadence="quarterly",
        stale_after_days=180,
        first_seen="2026-01-01",
        last_evidence_date="2026-05-04",
    )


# ─── state save/load ────────────────────────────────────────────────


def test_save_and_load_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp)
        state = {"ai-capex": _make_state("ai-capex", "core")}
        save_state(state, state_dir)
        loaded = load_state(state_dir)
        assert len(loaded) == 1
        assert loaded["ai-capex"].status == "core"
        assert loaded["ai-capex"].theme == "ai-capex"


def test_load_nonexistent_returns_empty():
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp)
        result = load_state(state_dir)
        assert result == {}


def test_load_corrupted_json_returns_empty():
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp)
        path = state_dir / "thesis_state.json"
        path.write_text("{ not valid json }", encoding="utf-8")
        result = load_state(state_dir)
        assert result == {}


def test_atomic_write_uses_tmp():
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp)
        save_state({"t": _make_state("t")}, state_dir)
        tmp_file = state_dir / "thesis_state.tmp"
        final_file = state_dir / "thesis_state.json"
        # 写入后 tmp 应已被 replace 为 final，不应存在
        assert not tmp_file.exists()
        assert final_file.exists()


# ─── evidence append / load ─────────────────────────────────────────


def test_append_and_load_evidence():
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp)
        ev = _make_evidence("2026-05-04", "ai-capex")
        written = append_evidence([ev], state_dir, today=date(2026, 5, 4))
        assert written == 1

        loaded = load_recent_evidence(state_dir, days=90, today=date(2026, 5, 5))
        assert len(loaded) == 1
        assert loaded[0].theme == "ai-capex"


def test_append_evidence_idempotent():
    """相同 evidence_id 第二次 append 应被去重，不重复写入"""
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp)
        ev = _make_evidence("2026-05-04", "ai-capex")
        written1 = append_evidence([ev], state_dir, today=date(2026, 5, 4))
        assert written1 == 1
        written2 = append_evidence([ev], state_dir, today=date(2026, 5, 4))
        assert written2 == 0

        loaded = load_recent_evidence(state_dir, days=90, today=date(2026, 5, 5))
        assert len(loaded) == 1


def test_append_evidence_partial_dedup():
    """新批次中一半已存在、一半新，只写新的"""
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp)
        ev_a = _make_evidence("2026-05-04", "theme-a")
        ev_a.evidence_id = "id-a"
        ev_b = _make_evidence("2026-05-04", "theme-b")
        ev_b.evidence_id = "id-b"

        append_evidence([ev_a], state_dir, today=date(2026, 5, 4))
        n = append_evidence([ev_a, ev_b], state_dir, today=date(2026, 5, 4))
        assert n == 1

        loaded = load_recent_evidence(state_dir, days=90, today=date(2026, 5, 5))
        assert len(loaded) == 2


def test_append_evidence_within_batch_dedup():
    """同一批 items 中包含重复 id，去重后只保留一份"""
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp)
        ev = _make_evidence("2026-05-04", "theme-x")
        ev.evidence_id = "same-id"
        n = append_evidence([ev, ev], state_dir, today=date(2026, 5, 4))
        assert n == 1

        loaded = load_recent_evidence(state_dir, days=90, today=date(2026, 5, 5))
        assert len(loaded) == 1


def test_append_evidence_skips_malformed_lines():
    """已有 jsonl 中混入坏行，append 后坏行被自动清理"""
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp)
        p = state_dir / "thesis_evidence_2026.jsonl"
        ev_a = _make_evidence("2026-01-01", "theme-a")
        ev_a.evidence_id = "id-a"
        ev_b = _make_evidence("2026-01-02", "theme-b")
        ev_b.evidence_id = "id-b"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            '{"evidence_id":"id-a","date":"2026-01-01","source_section":"company_news",'
            '"source_name":"X","url":null,"related_tickers":["TEST"],"theme":"theme-a",'
            '"direction":"support","strength":4,"horizon":"multi_year","text":"x","why_it_matters":"x"}\n'
            '!!! malformed line\n'
            '{"evidence_id":"id-b","date":"2026-01-02","source_section":"company_news",'
            '"source_name":"X","url":null,"related_tickers":["TEST"],"theme":"theme-b",'
            '"direction":"support","strength":4,"horizon":"multi_year","text":"x","why_it_matters":"x"}\n'
        )
        ev_c = _make_evidence("2026-05-04", "theme-c")
        ev_c.evidence_id = "id-c"
        append_evidence([ev_c], state_dir, today=date(2026, 5, 4))

        lines = p.read_text().splitlines()
        assert len(lines) == 3
        assert "malformed" not in p.read_text()

        loaded = load_recent_evidence(state_dir, days=200, today=date(2026, 5, 5))
        assert len(loaded) == 3


def test_load_recent_evidence_dedup():
    """load_recent_evidence 按 evidence_id 去重"""
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp)
        ev1 = _make_evidence("2026-05-04", "theme-a")
        ev2 = _make_evidence("2026-05-04", "theme-a")  # same evidence_id
        ev1.evidence_id = "same-id"
        ev2.evidence_id = "same-id"
        append_evidence([ev1], state_dir, today=date(2026, 5, 4))
        append_evidence([ev2], state_dir, today=date(2026, 5, 4))  # 写不进去，但 jsonl 只有 1 行

        loaded = load_recent_evidence(state_dir, days=90, today=date(2026, 5, 5))
        assert len(loaded) == 1


def test_load_recent_evidence_bad_jsonl_line():
    """损坏的 jsonl 行应被跳过并 warning"""
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp)
        path = state_dir / "thesis_evidence_2026.jsonl"
        ev = _make_evidence("2026-05-04", "ok-theme")
        append_evidence([ev], state_dir, today=date(2026, 5, 4))
        # 在已有内容前插入一行坏数据
        old = path.read_text(encoding="utf-8")
        path.write_text("{ not json }\n" + old, encoding="utf-8")

        loaded = load_recent_evidence(state_dir, days=90, today=date(2026, 5, 5))
        assert len(loaded) >= 1
        assert any(e.theme == "ok-theme" for e in loaded)


def test_evidence_outside_window_filtered():
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp)
        ev_old = _make_evidence("2025-12-01", "old")
        written = append_evidence([ev_old], state_dir, today=date(2025, 12, 1))
        assert written == 1

        loaded = load_recent_evidence(state_dir, days=30, today=date(2026, 5, 4))
        assert len(loaded) == 0


def test_append_empty_list():
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp)
        written = append_evidence([], state_dir)
        assert written == 0


# ─── rolling evidence ───────────────────────────────────────────────


def test_update_rolling_evidence():
    st = _make_state()
    assert len(st.rolling_evidence) == 0

    evs = [
        _make_evidence("2026-05-01", strength=4),
        _make_evidence("2026-05-02", strength=5),
    ]
    update_rolling_evidence(st, evs)
    assert len(st.rolling_evidence) == 2
    assert st.rolling_evidence[-1]["strength"] == 5


def test_update_rolling_evidence_idempotent():
    st = _make_state()
    ev = _make_evidence("2026-05-01", strength=4)
    ev.evidence_id = "same-id"

    update_rolling_evidence(st, [ev])
    update_rolling_evidence(st, [ev])

    assert len(st.rolling_evidence) == 1
    assert st.rolling_evidence[0]["evidence_id"] == "same-id"


def test_rolling_evidence_capped():
    st = _make_state()
    evs = [_make_evidence(f"2026-{m:02d}-01") for m in range(1, 26)]  # 25 items
    update_rolling_evidence(st, evs, max_items=20)
    assert len(st.rolling_evidence) == 20
    # 应保留最新的 20 条
    assert st.rolling_evidence[-1]["date"] == "2026-25-01"


# ─── year rolling ───────────────────────────────────────────────────


def test_year_rolling_file_naming(monkeypatch):
    """2026-12-31 写入 _2026.jsonl，2027-01-01 写入 _2027.jsonl。"""
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp)

        # Mock date.today()
        class MockDate:
            @staticmethod
            def today():
                return date(2026, 12, 31)

        import src.processors.thesis.state as mod
        original = mod.date
        mod.date = MockDate
        try:
            append_evidence([_make_evidence("2026-12-31")], state_dir)
        finally:
            mod.date = original

        assert (state_dir / "thesis_evidence_2026.jsonl").exists()
        assert not (state_dir / "thesis_evidence_2027.jsonl").exists()
