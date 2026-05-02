"""tests/test_monitor.py — 监控脚本逻辑测试。"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

# 让 import scripts/monitor 工作
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


def _mock_api(monkeypatch, runs: list[dict]) -> None:
    body = json.dumps({"workflow_runs": runs}).encode()
    mock_resp = MagicMock()
    mock_resp.read.return_value = body
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = False
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: mock_resp)
    monkeypatch.setattr(
        "json.load",
        lambda fp: json.loads(body.decode()),
    )


def test_no_env_returns_not_ok(monkeypatch) -> None:
    """缺 GH_TOKEN / GH_REPO → 返回 not OK"""
    for k in ("GH_TOKEN", "GH_REPO"):
        monkeypatch.delenv(k, raising=False)
    from scripts import monitor
    ok, reason = monitor.check_today_status()
    assert ok is False
    assert "缺少" in reason


def test_today_success_returns_ok(monkeypatch) -> None:
    """今天有 success run → OK"""
    monkeypatch.setenv("GH_TOKEN", "x")
    monkeypatch.setenv("GH_REPO", "owner/repo")
    _mock_api(monkeypatch, [
        {"id": 100, "conclusion": "success", "status": "completed",
         "created_at": f"{_today()}T22:30:00Z"},
    ])
    from scripts import monitor
    ok, reason = monitor.check_today_status()
    assert ok is True
    assert "已成功" in reason


def test_today_in_progress_returns_ok(monkeypatch) -> None:
    """今天有 in_progress run(daily.yml 还在跑)→ OK,不误报"""
    monkeypatch.setenv("GH_TOKEN", "x")
    monkeypatch.setenv("GH_REPO", "owner/repo")
    _mock_api(monkeypatch, [
        {"id": 100, "conclusion": None, "status": "in_progress",
         "created_at": f"{_today()}T23:00:00Z"},
    ])
    from scripts import monitor
    ok, reason = monitor.check_today_status()
    assert ok is True
    assert "进行中" in reason


def test_today_no_runs_returns_not_ok(monkeypatch) -> None:
    """今天完全没 run(cron 没触发 / PAT 过期没创建 run)→ not OK,告警"""
    monkeypatch.setenv("GH_TOKEN", "x")
    monkeypatch.setenv("GH_REPO", "owner/repo")
    _mock_api(monkeypatch, [
        # 昨天的 run,不算
        {"id": 99, "conclusion": "success", "status": "completed",
         "created_at": "2024-01-01T22:30:00Z"},
    ])
    from scripts import monitor
    ok, reason = monitor.check_today_status()
    assert ok is False
    assert "无 daily.yml run" in reason


def test_today_all_failed_returns_not_ok(monkeypatch) -> None:
    """今天 run 都失败 → not OK,告警"""
    monkeypatch.setenv("GH_TOKEN", "x")
    monkeypatch.setenv("GH_REPO", "owner/repo")
    _mock_api(monkeypatch, [
        {"id": 100, "conclusion": "failure", "status": "completed",
         "created_at": f"{_today()}T22:30:00Z"},
        {"id": 101, "conclusion": "cancelled", "status": "completed",
         "created_at": f"{_today()}T23:00:00Z"},
    ])
    from scripts import monitor
    ok, reason = monitor.check_today_status()
    assert ok is False
    assert "失败" in reason


def test_api_failure_returns_not_ok(monkeypatch) -> None:
    """API 调用失败 → not OK,告警(因为我们无法判定,保守告警)"""
    monkeypatch.setenv("GH_TOKEN", "x")
    monkeypatch.setenv("GH_REPO", "owner/repo")
    with patch("urllib.request.urlopen", side_effect=Exception("network error")):
        from scripts import monitor
        ok, reason = monitor.check_today_status()
        assert ok is False
        assert "API 调用失败" in reason
