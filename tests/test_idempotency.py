"""tests/test_idempotency.py — 双触发幂等性测试。"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from src.utils import idempotency
from src.utils.idempotency import already_sent_today


def _clean_env(monkeypatch) -> None:
    """清掉所有 GH 环境变量,确保单测从干净起点开始。"""
    for k in ("GH_TOKEN", "GH_REPO", "GH_RUN_ID", "GH_EVENT_NAME"):
        monkeypatch.delenv(k, raising=False)


def test_local_run_returns_false(monkeypatch) -> None:
    """本地运行无 GH_TOKEN → 返回 False(允许发送)"""
    _clean_env(monkeypatch)
    assert already_sent_today() is False


def test_workflow_dispatch_also_dedups(monkeypatch) -> None:
    """workflow_dispatch 也参与幂等检查 — 避免 cron-job.org 外部触发 + GH schedule 兜底双发。

    当外部触发器(cron-job.org → workflow_dispatch)调用时,如果当日已有成功
    run(无论是 schedule 还是 workflow_dispatch),应当跳过本次。
    """
    _clean_env(monkeypatch)
    monkeypatch.setenv("GH_TOKEN", "x")
    monkeypatch.setenv("GH_REPO", "owner/repo")
    monkeypatch.setenv("GH_EVENT_NAME", "workflow_dispatch")
    monkeypatch.setenv("GH_RUN_ID", "999")  # 当前 run

    today = idempotency._today_utc_iso()
    _mock_api_response(monkeypatch, [
        # 别的 run(GH schedule 兜底先成功了),今天成功
        {"id": 100, "conclusion": "success", "created_at": f"{today}T22:30:00Z"},
    ])
    assert already_sent_today() is True


def test_api_failure_returns_true_fail_close(monkeypatch) -> None:
    """API 调用失败 → 返回 True(fail-close,保守跳过本次发送)。

    取舍:GH API 抖动时,宁可漏发一次也不双发(漏发用户会察觉,双发更打扰)。
    """
    _clean_env(monkeypatch)
    monkeypatch.setenv("GH_TOKEN", "x")
    monkeypatch.setenv("GH_REPO", "owner/repo")
    monkeypatch.setenv("GH_EVENT_NAME", "schedule")
    monkeypatch.setenv("GH_RUN_ID", "100")

    with patch("urllib.request.urlopen", side_effect=Exception("network error")):
        assert already_sent_today() is True


def test_in_progress_run_counts(monkeypatch) -> None:
    """今日有 in_progress 的 run(尚未完成)→ True(避免 TOCTOU 双发)。

    场景:cron-job.org 7:00 触发 Run A,GH schedule 6:30 cron 延迟到 ~7:00
    触发 Run B。两者并发启动,A 先调用 already_sent_today() 时 B 还没创建,
    返回 False → A 继续。30 秒后 B 启动,调用 already_sent_today(),此时
    A 还在跑(status=in_progress,conclusion=null),仅看 conclusion=success
    会漏判 → B 也继续 → 双发。in_progress 也算就避免这个。
    """
    _clean_env(monkeypatch)
    monkeypatch.setenv("GH_TOKEN", "x")
    monkeypatch.setenv("GH_REPO", "owner/repo")
    monkeypatch.setenv("GH_EVENT_NAME", "workflow_dispatch")
    monkeypatch.setenv("GH_RUN_ID", "999")

    today = idempotency._today_utc_iso()
    _mock_api_response(monkeypatch, [
        # A run 还在跑
        {"id": 100, "status": "in_progress", "conclusion": None,
         "created_at": f"{today}T22:30:30Z"},
    ])
    assert already_sent_today() is True


def test_queued_run_counts(monkeypatch) -> None:
    """今日有 queued 的 run(刚被 GH 创建,还在排队)→ True"""
    _clean_env(monkeypatch)
    monkeypatch.setenv("GH_TOKEN", "x")
    monkeypatch.setenv("GH_REPO", "owner/repo")
    monkeypatch.setenv("GH_EVENT_NAME", "schedule")
    monkeypatch.setenv("GH_RUN_ID", "999")

    today = idempotency._today_utc_iso()
    _mock_api_response(monkeypatch, [
        {"id": 100, "status": "queued", "conclusion": None,
         "created_at": f"{today}T23:00:05Z"},
    ])
    assert already_sent_today() is True


def _mock_api_response(monkeypatch, runs: list[dict]) -> None:
    """注入假的 GitHub API 响应。"""
    body = json.dumps({"workflow_runs": runs}).encode()
    mock_resp = MagicMock()
    mock_resp.read.return_value = body
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = False
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: mock_resp)
    # urlopen 返回的对象 read() 是 bytes,但 json.load 需要可读文件对象
    monkeypatch.setattr(
        "json.load",
        lambda fp: json.loads(body.decode()),
    )


def test_today_success_returns_true(monkeypatch) -> None:
    """今日(UTC)有成功的 schedule run → True(应该跳过)"""
    _clean_env(monkeypatch)
    monkeypatch.setenv("GH_TOKEN", "x")
    monkeypatch.setenv("GH_REPO", "owner/repo")
    monkeypatch.setenv("GH_EVENT_NAME", "schedule")
    monkeypatch.setenv("GH_RUN_ID", "999")  # 当前 run

    today = idempotency._today_utc_iso()
    _mock_api_response(monkeypatch, [
        # 别的 run(不是当前),今天成功
        {"id": 100, "conclusion": "success", "created_at": f"{today}T23:08:00Z"},
    ])
    assert already_sent_today() is True


def test_excludes_current_run(monkeypatch) -> None:
    """当前 run 即使成功也不算(自己不能算自己已发)"""
    _clean_env(monkeypatch)
    monkeypatch.setenv("GH_TOKEN", "x")
    monkeypatch.setenv("GH_REPO", "owner/repo")
    monkeypatch.setenv("GH_EVENT_NAME", "schedule")
    monkeypatch.setenv("GH_RUN_ID", "100")

    today = idempotency._today_utc_iso()
    _mock_api_response(monkeypatch, [
        {"id": 100, "conclusion": "success", "created_at": f"{today}T23:08:00Z"},
    ])
    assert already_sent_today() is False


def test_failed_run_doesnt_count(monkeypatch) -> None:
    """失败的 run 不算已发"""
    _clean_env(monkeypatch)
    monkeypatch.setenv("GH_TOKEN", "x")
    monkeypatch.setenv("GH_REPO", "owner/repo")
    monkeypatch.setenv("GH_EVENT_NAME", "schedule")
    monkeypatch.setenv("GH_RUN_ID", "999")

    today = idempotency._today_utc_iso()
    _mock_api_response(monkeypatch, [
        {"id": 100, "conclusion": "failure", "created_at": f"{today}T23:08:00Z"},
    ])
    assert already_sent_today() is False


def test_yesterday_doesnt_count(monkeypatch) -> None:
    """昨天的成功 run 不算今天"""
    _clean_env(monkeypatch)
    monkeypatch.setenv("GH_TOKEN", "x")
    monkeypatch.setenv("GH_REPO", "owner/repo")
    monkeypatch.setenv("GH_EVENT_NAME", "schedule")
    monkeypatch.setenv("GH_RUN_ID", "999")

    _mock_api_response(monkeypatch, [
        # 2 天前的成功 run
        {"id": 100, "conclusion": "success", "created_at": "2024-01-01T23:08:00Z"},
    ])
    assert already_sent_today() is False
