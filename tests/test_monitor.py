"""tests/test_monitor.py — 监控脚本逻辑测试。"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.utils.dates import BEIJING

# 让 import scripts/monitor 工作
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _today() -> str:
    """BJT today — monitor 与 idempotency 同口径。"""
    return datetime.now(BEIJING).date().isoformat()


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
    """今天有 success run → OK
    BJT today 12:00 = UTC today 04:00,确保 created_at 转 BJT 后仍是 today。
    """
    monkeypatch.setenv("GH_TOKEN", "x")
    monkeypatch.setenv("GH_REPO", "owner/repo")
    _mock_api(monkeypatch, [
        {"id": 100, "conclusion": "success", "status": "completed",
         "created_at": f"{_today()}T04:00:00Z"},
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
         "created_at": f"{_today()}T04:00:00Z"},
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
         "created_at": "2024-01-01T04:00:00Z"},
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
         "created_at": f"{_today()}T04:00:00Z"},
        {"id": 101, "conclusion": "cancelled", "status": "completed",
         "created_at": f"{_today()}T04:30:00Z"},
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


def test_cross_utc_midnight_same_bjt_day(monkeypatch) -> None:
    """监控的 P0 回归测试 — cron 在 BJT 06:30 触发(=UTC 22:30 前一日),
    monitor 在 BJT 08:30 (=UTC 00:30) 检查时若用 UTC 比对会误报"今日无 run"。
    现在用 BJT 比对应识别为同一 BJT 日 → OK。
    """
    from datetime import datetime, timedelta
    monkeypatch.setenv("GH_TOKEN", "x")
    monkeypatch.setenv("GH_REPO", "owner/repo")
    # BJT today 06:30 = UTC yesterday 22:30
    bjt_today = datetime.fromisoformat(_today())
    utc_yesterday_evening = bjt_today - timedelta(hours=8) + timedelta(hours=6, minutes=30)
    iso_str = utc_yesterday_evening.strftime("%Y-%m-%dT%H:%M:%SZ")
    _mock_api(monkeypatch, [
        {"id": 100, "conclusion": "success", "status": "completed",
         "created_at": iso_str},
    ])
    from scripts import monitor
    ok, reason = monitor.check_today_status()
    assert ok is True, (
        f"BJT today 06:30 (created_at={iso_str}) 应识别为同一 BJT 日 → 不告警"
    )
