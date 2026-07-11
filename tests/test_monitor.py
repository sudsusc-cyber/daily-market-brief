"""tests/test_monitor.py — 监控脚本逻辑测试。"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from src.utils.dates import BEIJING

# 让 import scripts/monitor 工作
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import monitor


def _today() -> str:
    """BJT today — monitor 与 idempotency 同口径。"""
    return datetime.now(BEIJING).date().isoformat()


@pytest.fixture(autouse=True)
def _market_is_open(monkeypatch) -> None:
    monkeypatch.setattr(monitor, "should_send_today", lambda _today: (True, "正常交易"))


def _mock_api(monkeypatch, runs: list[dict], *, delivered_ids: set[int] | None = None) -> None:
    delivered_ids = delivered_ids or set()

    def fake_github_json(url: str, _token: str) -> dict:
        if "/jobs" not in url:
            return {"workflow_runs": runs}
        run_id = int(url.split("/runs/", 1)[1].split("/", 1)[0])
        conclusion = "success" if run_id in delivered_ids else "skipped"
        return {
            "jobs": [{
                "steps": [{"name": "Confirm email delivery", "conclusion": conclusion}],
            }],
        }

    monkeypatch.setattr(monitor, "_github_json", fake_github_json)


def test_no_env_returns_not_ok(monkeypatch) -> None:
    """缺 GH_TOKEN / GH_REPO → 返回 not OK"""
    for k in ("GH_TOKEN", "GH_REPO"):
        monkeypatch.delenv(k, raising=False)
    ok, reason = monitor.check_today_status()
    assert ok is False
    assert "缺少" in reason


def test_invalid_repo_slug_returns_not_ok(monkeypatch) -> None:
    monkeypatch.setenv("GH_TOKEN", "x")
    monkeypatch.setenv("GH_REPO", "file:///tmp/not-a-repo")

    ok, reason = monitor.check_today_status()

    assert ok is False
    assert "owner/repo" in reason


def test_today_success_returns_ok(monkeypatch) -> None:
    """今天有 success run → OK
    BJT today 12:00 = UTC today 04:00,确保 created_at 转 BJT 后仍是 today。
    """
    monkeypatch.setenv("GH_TOKEN", "x")
    monkeypatch.setenv("GH_REPO", "owner/repo")
    _mock_api(monkeypatch, [
        {"id": 100, "conclusion": "success", "status": "completed",
         "created_at": f"{_today()}T04:00:00Z"},
    ], delivered_ids={100})
    ok, reason = monitor.check_today_status()
    assert ok is True
    assert "确认送达" in reason


def test_today_in_progress_without_receipt_returns_not_ok(monkeypatch) -> None:
    """监控执行时仍未完成且没有送达凭证，应告警而不是永久放过。"""
    monkeypatch.setenv("GH_TOKEN", "x")
    monkeypatch.setenv("GH_REPO", "owner/repo")
    _mock_api(monkeypatch, [
        {"id": 100, "conclusion": None, "status": "in_progress",
         "created_at": f"{_today()}T04:00:00Z"},
    ])
    ok, reason = monitor.check_today_status()
    assert ok is False
    assert "仍未完成" in reason


def test_successful_run_without_delivery_receipt_returns_not_ok(monkeypatch) -> None:
    """幂等 fail-close 等路径虽 exit 0，但没有凭证时不能当成已送达。"""
    monkeypatch.setenv("GH_TOKEN", "x")
    monkeypatch.setenv("GH_REPO", "owner/repo")
    _mock_api(monkeypatch, [
        {"id": 100, "conclusion": "success", "status": "completed",
         "created_at": f"{_today()}T04:00:00Z"},
    ])
    ok, reason = monitor.check_today_status()
    assert ok is False
    assert "无邮件送达确认" in reason


def test_today_no_runs_returns_not_ok(monkeypatch) -> None:
    """今天完全没 run(cron 没触发 / PAT 过期没创建 run)→ not OK,告警"""
    monkeypatch.setenv("GH_TOKEN", "x")
    monkeypatch.setenv("GH_REPO", "owner/repo")
    _mock_api(monkeypatch, [
        # 昨天的 run,不算
        {"id": 99, "conclusion": "success", "status": "completed",
         "created_at": "2024-01-01T04:00:00Z"},
    ])
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
    ok, reason = monitor.check_today_status()
    assert ok is False
    assert "无邮件送达确认" in reason


def test_api_failure_returns_not_ok(monkeypatch) -> None:
    """API 调用失败 → not OK,告警(因为我们无法判定,保守告警)"""
    monkeypatch.setenv("GH_TOKEN", "x")
    monkeypatch.setenv("GH_REPO", "owner/repo")
    with patch.object(monitor, "_github_json", side_effect=Exception("network error")):
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
    ], delivered_ids={100})
    ok, reason = monitor.check_today_status()
    assert ok is True, (
        f"BJT today 06:30 (created_at={iso_str}) 应识别为同一 BJT 日 → 不告警"
    )


def test_market_holiday_is_ok_without_github_env(monkeypatch) -> None:
    monkeypatch.setattr(monitor, "should_send_today", lambda _today: (False, "美股休市"))
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GH_REPO", raising=False)

    ok, reason = monitor.check_today_status()

    assert ok is True
    assert "无需发送" in reason


def test_send_alert_needs_only_email_settings(monkeypatch) -> None:
    for key in ("FINNHUB_API_KEY", "FRED_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("QQ_EMAIL_ADDRESS", "sender@qq.com")
    monkeypatch.setenv("QQ_EMAIL_AUTH_CODE", "auth")
    monkeypatch.setenv("EMAIL_RECIPIENT", "recipient@qq.com")
    monkeypatch.setenv("GH_REPO", "owner/repo")
    sent: list[dict] = []
    monkeypatch.setattr(monitor, "send_html_email", lambda **kwargs: sent.append(kwargs))

    monitor.send_alert("测试告警")

    assert len(sent) == 1
    assert sent[0]["recipient"] == ["recipient@qq.com"]


def test_monitor_main_propagates_alert_send_failure(monkeypatch) -> None:
    monkeypatch.setattr(monitor, "check_today_status", lambda: (False, "未送达"))
    monkeypatch.setattr(
        monitor,
        "send_alert",
        lambda _reason: (_ for _ in ()).throw(RuntimeError("smtp down")),
    )

    with pytest.raises(RuntimeError, match="smtp down"):
        monitor.main()


def test_duplicate_monitor_trigger_skips_second_alert(tmp_path, monkeypatch) -> None:
    path = tmp_path / "alert.txt"
    path.write_text("未送达", encoding="utf-8")
    monkeypatch.setenv("MONITOR_ALERT_PATH", str(path))
    monkeypatch.setenv("GH_TOKEN", "x")
    monkeypatch.setenv("GH_REPO", "owner/repo")
    monkeypatch.setenv("GH_RUN_ID", "200")

    def fake_json(url: str, _token: str) -> dict:
        if "/jobs" not in url:
            return {"workflow_runs": [{
                "id": 100,
                "head_branch": "main",
                "created_at": f"{_today()}T04:00:00Z",
            }]}
        return {"jobs": [{"steps": [{
            "name": "Send monitor alert", "conclusion": "success",
        }]}]}

    monkeypatch.setattr(monitor, "_github_json", fake_json)
    sent: list[str] = []
    monkeypatch.setattr(monitor, "send_alert", lambda reason: sent.append(reason))

    assert monitor.send_requested_alert() == 0
    assert sent == []


def test_check_only_writes_alert_request(tmp_path, monkeypatch) -> None:
    path = tmp_path / "alert.txt"
    monkeypatch.setenv("MONITOR_ALERT_PATH", str(path))
    monkeypatch.setattr(monitor, "check_today_status", lambda: (False, "缺少邮件"))

    assert monitor.check_only() == 0
    assert path.read_text(encoding="utf-8") == "缺少邮件"
