"""buffett_13f 延后写盘接口的单元测试。

不调 SEC EDGAR(全部 mock _fetch_atom)。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from src.collectors import buffett_13f
from src.collectors.buffett_13f import Filing13F


def _filing(accession: str, days_ago: int = 1) -> Filing13F:
    return Filing13F(
        accession_no=accession,
        filed_at=datetime.now(UTC) - timedelta(days=days_ago),
        title="13F-HR",
    )


# ─── fetch ─────────────────────────────────────────────────────────


def test_fetch_no_state_first_run_returns_pending(tmp_path: Path) -> None:
    """state 不存在 → is_new=True + pending_save 非空。"""
    state_path = tmp_path / "last_13f.json"
    with mock.patch.object(buffett_13f, "_fetch_atom", return_value=[_filing("A-1")]):
        bundle, pending = buffett_13f.fetch(state_path=state_path)
    assert bundle.error is None
    assert bundle.is_new is True
    assert pending is not None
    assert pending["accession_no"] == "A-1"
    # 关键:fetch 不写盘
    assert not state_path.exists()


def test_fetch_same_filing_no_pending(tmp_path: Path) -> None:
    """state 已记录同一 accession → is_new=False + pending_save=None。"""
    state_path = tmp_path / "last_13f.json"
    state_path.write_text(json.dumps({"accession_no": "A-1"}), encoding="utf-8")
    with mock.patch.object(buffett_13f, "_fetch_atom", return_value=[_filing("A-1")]):
        bundle, pending = buffett_13f.fetch(state_path=state_path)
    assert bundle.is_new is False
    assert pending is None


def test_fetch_new_filing_outside_window(tmp_path: Path) -> None:
    """新 filing 但已超过展示窗口 → is_new=False (但 pending_save 仍非空)。"""
    state_path = tmp_path / "last_13f.json"
    state_path.write_text(json.dumps({"accession_no": "OLD"}), encoding="utf-8")
    with mock.patch.object(
        buffett_13f, "_fetch_atom",
        return_value=[_filing("A-NEW", days_ago=30)],
    ):
        bundle, pending = buffett_13f.fetch(state_path=state_path, display_window_days=7)
    assert bundle.is_new is False  # 超窗口
    assert pending is not None      # 但仍要 commit,避免下次 run 重判
    assert pending["accession_no"] == "A-NEW"


def test_fetch_atom_failure_returns_error_no_pending(tmp_path: Path) -> None:
    state_path = tmp_path / "last_13f.json"
    with mock.patch.object(buffett_13f, "_fetch_atom", side_effect=RuntimeError("EDGAR 503")):
        bundle, pending = buffett_13f.fetch(state_path=state_path)
    assert bundle.error is not None
    assert "RuntimeError" in bundle.error
    assert pending is None
    assert not state_path.exists()


def test_fetch_empty_atom_returns_error(tmp_path: Path) -> None:
    state_path = tmp_path / "last_13f.json"
    with mock.patch.object(buffett_13f, "_fetch_atom", return_value=[]):
        bundle, pending = buffett_13f.fetch(state_path=state_path)
    assert bundle.error == "EDGAR atom 无条目"
    assert pending is None


# ─── commit_pushed ─────────────────────────────────────────────────


def test_commit_pushed_writes_state(tmp_path: Path) -> None:
    state_path = tmp_path / "last_13f.json"
    payload = {
        "accession_no": "A-1",
        "filed_at": "2026-05-01T00:00:00+00:00",
        "first_seen_at": "2026-05-02T00:00:00+00:00",
        "title": "13F-HR",
    }
    buffett_13f.commit_pushed(state_path, payload)
    assert state_path.exists()
    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert saved["accession_no"] == "A-1"
    assert saved["title"] == "13F-HR"


def test_commit_pushed_none_is_noop(tmp_path: Path) -> None:
    state_path = tmp_path / "last_13f.json"
    buffett_13f.commit_pushed(state_path, None)
    assert not state_path.exists()


def test_commit_pushed_empty_dict_is_noop(tmp_path: Path) -> None:
    """空 dict 也视作 falsy,no-op(防御性:fetch 路径不会产出空 dict)。"""
    state_path = tmp_path / "last_13f.json"
    buffett_13f.commit_pushed(state_path, {})
    assert not state_path.exists()


def test_commit_pushed_atomic_write(tmp_path: Path) -> None:
    """commit_pushed 用 .tmp + replace,中途无半文件残留。"""
    state_path = tmp_path / "last_13f.json"
    state_path.write_text("OLD", encoding="utf-8")
    buffett_13f.commit_pushed(state_path, {"accession_no": "NEW"})
    # 写入后 .tmp 文件应消失
    assert not state_path.with_suffix(state_path.suffix + ".tmp").exists()
    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert saved["accession_no"] == "NEW"


# ─── 端到端:fetch + commit_pushed ──────────────────────────────────


def test_fetch_then_commit_makes_next_run_idempotent(tmp_path: Path) -> None:
    """fetch → commit → 再 fetch:第二次应当 is_new=False。"""
    state_path = tmp_path / "last_13f.json"
    filings = [_filing("A-1")]
    with mock.patch.object(buffett_13f, "_fetch_atom", return_value=filings):
        _, pending1 = buffett_13f.fetch(state_path=state_path)
    buffett_13f.commit_pushed(state_path, pending1)

    with mock.patch.object(buffett_13f, "_fetch_atom", return_value=filings):
        bundle2, pending2 = buffett_13f.fetch(state_path=state_path)
    assert bundle2.is_new is False
    assert pending2 is None


def test_fetch_without_commit_replays_next_run(tmp_path: Path) -> None:
    """fetch → 不 commit(模拟 SMTP 失败) → 再 fetch:仍 is_new=True,可重发。

    这是延后写盘的核心动机:邮件失败时不能让 state 提前固化,否则用户永远收不到。
    """
    state_path = tmp_path / "last_13f.json"
    filings = [_filing("A-1")]
    with mock.patch.object(buffett_13f, "_fetch_atom", return_value=filings):
        bundle1, pending1 = buffett_13f.fetch(state_path=state_path)
    assert bundle1.is_new is True
    assert pending1 is not None
    # 模拟 SMTP 失败:**不**调 commit_pushed

    with mock.patch.object(buffett_13f, "_fetch_atom", return_value=filings):
        bundle2, pending2 = buffett_13f.fetch(state_path=state_path)
    assert bundle2.is_new is True   # 仍判为新,允许重发
    assert pending2 is not None      # 又一次 pending
