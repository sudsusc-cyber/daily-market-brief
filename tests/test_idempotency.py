"""tests/test_idempotency.py — 双触发幂等性测试。"""

from __future__ import annotations

import json
import os
from unittest.mock import patch, MagicMock

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


def test_workflow_dispatch_skips_check(monkeypatch) -> None:
    """workflow_dispatch 手动触发不参与幂等(便于调试)"""
    _clean_env(monkeypatch)
    monkeypatch.setenv("GH_TOKEN", "x")
    monkeypatch.setenv("GH_REPO", "owner/repo")
    monkeypatch.setenv("GH_EVENT_NAME", "workflow_dispatch")
    assert already_sent_today() is False


def test_api_failure_returns_false(monkeypatch) -> None:
    """API 调用失败 → 返回 False(不阻塞,允许发送)"""
    _clean_env(monkeypatch)
    monkeypatch.setenv("GH_TOKEN", "x")
    monkeypatch.setenv("GH_REPO", "owner/repo")
    monkeypatch.setenv("GH_EVENT_NAME", "schedule")
    monkeypatch.setenv("GH_RUN_ID", "100")

    with patch("urllib.request.urlopen", side_effect=Exception("network error")):
        assert already_sent_today() is False


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
