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

    today = idempotency._today_beijing_iso()
    _mock_api_response(monkeypatch, [
        # 别的 run(GH schedule 兜底先成功了),今天成功
        # BJT today 12:00 = UTC today 04:00,确保 created_at 转 BJT 后仍是 today
        {"id": 100, "conclusion": "success", "created_at": f"{today}T04:00:00Z"},
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

    today = idempotency._today_beijing_iso()
    _mock_api_response(monkeypatch, [
        # A run 还在跑;BJT today 12:00 = UTC today 04:00
        {"id": 100, "status": "in_progress", "conclusion": None,
         "created_at": f"{today}T04:00:30Z"},
    ])
    assert already_sent_today() is True


def test_queued_run_counts(monkeypatch) -> None:
    """今日有 queued 的 run(刚被 GH 创建,还在排队)→ True"""
    _clean_env(monkeypatch)
    monkeypatch.setenv("GH_TOKEN", "x")
    monkeypatch.setenv("GH_REPO", "owner/repo")
    monkeypatch.setenv("GH_EVENT_NAME", "schedule")
    monkeypatch.setenv("GH_RUN_ID", "999")

    today = idempotency._today_beijing_iso()
    _mock_api_response(monkeypatch, [
        {"id": 100, "status": "queued", "conclusion": None,
         "created_at": f"{today}T04:00:05Z"},
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

    today = idempotency._today_beijing_iso()
    _mock_api_response(monkeypatch, [
        # 别的 run(不是当前),今天成功;BJT today 12:00 = UTC today 04:00
        {"id": 100, "conclusion": "success", "created_at": f"{today}T04:08:00Z"},
    ])
    assert already_sent_today() is True


def test_excludes_current_run(monkeypatch) -> None:
    """当前 run 即使成功也不算(自己不能算自己已发)"""
    _clean_env(monkeypatch)
    monkeypatch.setenv("GH_TOKEN", "x")
    monkeypatch.setenv("GH_REPO", "owner/repo")
    monkeypatch.setenv("GH_EVENT_NAME", "schedule")
    monkeypatch.setenv("GH_RUN_ID", "100")

    today = idempotency._today_beijing_iso()
    _mock_api_response(monkeypatch, [
        {"id": 100, "conclusion": "success", "created_at": f"{today}T04:08:00Z"},
    ])
    assert already_sent_today() is False


def test_failed_run_doesnt_count(monkeypatch) -> None:
    """失败的 run 不算已发"""
    _clean_env(monkeypatch)
    monkeypatch.setenv("GH_TOKEN", "x")
    monkeypatch.setenv("GH_REPO", "owner/repo")
    monkeypatch.setenv("GH_EVENT_NAME", "schedule")
    monkeypatch.setenv("GH_RUN_ID", "999")

    today = idempotency._today_beijing_iso()
    _mock_api_response(monkeypatch, [
        {"id": 100, "conclusion": "failure", "created_at": f"{today}T04:08:00Z"},
    ])
    assert already_sent_today() is False


def test_cross_utc_midnight_same_bjt_day(monkeypatch) -> None:
    """跨 UTC 0 点但同 BJT 日的 run → True(回归测试 P0 cross-day bug)。

    场景:cron 在 BJT 06:30 触发 = UTC 22:30(前一日)。两个触发器一前一后:
      Run A: BJT today 06:30 = UTC yesterday 22:30:00Z
      Run B: BJT today 06:30:30 = UTC today 22:30:30Z(若 cron-job.org 慢半拍跨过 UTC 0 点)
    旧 UTC 比较会因 created_at 不同 UTC 日 → 漏判 → 双发。
    新 BJT 比较应识别为同一 BJT 日 → True。

    由于不能伪造 datetime.now,这里直接构造一个 created_at 对应的 UTC 字符串,
    其在 BJT 视角等于 _today_beijing_iso() 但 UTC 视角是昨天。
    """
    _clean_env(monkeypatch)
    monkeypatch.setenv("GH_TOKEN", "x")
    monkeypatch.setenv("GH_REPO", "owner/repo")
    monkeypatch.setenv("GH_EVENT_NAME", "workflow_dispatch")
    monkeypatch.setenv("GH_RUN_ID", "999")

    # BJT today 00:30 = UTC yesterday 16:30 — 跨 UTC 日但同 BJT 日
    from datetime import datetime, timedelta
    bjt_today = datetime.fromisoformat(idempotency._today_beijing_iso())
    utc_yesterday_evening = (bjt_today - timedelta(hours=8) + timedelta(hours=0, minutes=30))
    iso_str = utc_yesterday_evening.strftime("%Y-%m-%dT%H:%M:%SZ")
    _mock_api_response(monkeypatch, [
        {"id": 100, "conclusion": "success", "created_at": iso_str},
    ])
    assert already_sent_today() is True, (
        f"BJT today 00:30 (created_at={iso_str}) 应识别为同一 BJT 日"
    )


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


def test_leader_smallest_run_id_sends(monkeypatch) -> None:
    """两个并发 in_progress run:run_id 较小者(=最早创建)是 leader → 发邮件。"""
    _clean_env(monkeypatch)
    monkeypatch.setenv("GH_TOKEN", "x")
    monkeypatch.setenv("GH_REPO", "owner/repo")
    monkeypatch.setenv("GH_EVENT_NAME", "schedule")
    monkeypatch.setenv("GH_RUN_ID", "100")  # 自己更小

    today = idempotency._today_beijing_iso()
    _mock_api_response(monkeypatch, [
        # 另一个 in_progress run id=200(更大,follower)
        {"id": 200, "status": "in_progress", "conclusion": None,
         "created_at": f"{today}T04:00:30Z"},
    ])
    # leader 应当发 → already_sent_today() False(放行)
    assert already_sent_today() is False


def test_follower_larger_run_id_skips(monkeypatch) -> None:
    """两个并发 in_progress run:run_id 较大者(=较晚创建)是 follower → 让位 skip。

    这是 ultrareview P1 修复的核心 — 旧版会双双 skip(都看到对方 in_progress)
    导致没人发邮件;leader election 保证恰好有一个发。
    """
    _clean_env(monkeypatch)
    monkeypatch.setenv("GH_TOKEN", "x")
    monkeypatch.setenv("GH_REPO", "owner/repo")
    monkeypatch.setenv("GH_EVENT_NAME", "workflow_dispatch")
    monkeypatch.setenv("GH_RUN_ID", "200")  # 自己更大

    today = idempotency._today_beijing_iso()
    _mock_api_response(monkeypatch, [
        # 另一个 in_progress run id=100(更小,leader)
        {"id": 100, "status": "in_progress", "conclusion": None,
         "created_at": f"{today}T04:00:00Z"},
    ])
    # follower 让位 → already_sent_today() True(skip)
    assert already_sent_today() is True


def test_three_concurrent_only_min_id_runs(monkeypatch) -> None:
    """三个并发 run:只有最小 run_id 发,其他两个 skip。"""
    _clean_env(monkeypatch)
    monkeypatch.setenv("GH_TOKEN", "x")
    monkeypatch.setenv("GH_REPO", "owner/repo")
    monkeypatch.setenv("GH_EVENT_NAME", "workflow_dispatch")
    today = idempotency._today_beijing_iso()
    others = [
        {"id": 50, "status": "in_progress", "conclusion": None,
         "created_at": f"{today}T04:00:00Z"},  # leader
        {"id": 999, "status": "queued", "conclusion": None,
         "created_at": f"{today}T04:00:30Z"},
    ]
    _mock_api_response(monkeypatch, others)

    # 最小 id (50) 是 leader,自己 = 50 → 发
    monkeypatch.setenv("GH_RUN_ID", "50")
    _mock_api_response(monkeypatch, [
        {"id": 999, "status": "queued", "conclusion": None,
         "created_at": f"{today}T04:00:30Z"},
        {"id": 100, "status": "in_progress", "conclusion": None,
         "created_at": f"{today}T04:00:15Z"},
    ])
    assert already_sent_today() is False  # 50 < {999, 100} → leader

    # 自己是中间 id (100) → follower
    monkeypatch.setenv("GH_RUN_ID", "100")
    _mock_api_response(monkeypatch, [
        {"id": 50, "status": "in_progress", "conclusion": None,
         "created_at": f"{today}T04:00:00Z"},
        {"id": 999, "status": "queued", "conclusion": None,
         "created_at": f"{today}T04:00:30Z"},
    ])
    assert already_sent_today() is True  # 100 > 50 → follower


def test_leader_election_ignores_yesterday_runs(monkeypatch) -> None:
    """昨日 run 不参与 today leader election。"""
    _clean_env(monkeypatch)
    monkeypatch.setenv("GH_TOKEN", "x")
    monkeypatch.setenv("GH_REPO", "owner/repo")
    monkeypatch.setenv("GH_EVENT_NAME", "schedule")
    monkeypatch.setenv("GH_RUN_ID", "200")  # 较大,但今日唯一 active

    _mock_api_response(monkeypatch, [
        # 昨日有个更小 id 的 in_progress(从未完成,卡住的 run)
        {"id": 50, "status": "in_progress", "conclusion": None,
         "created_at": "2024-01-01T04:00:00Z"},
        # 今日还有自己 200 一个
    ])
    # 200 是今日唯一 active → leader → 发
    assert already_sent_today() is False
