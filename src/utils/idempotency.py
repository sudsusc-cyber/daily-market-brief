"""
当日已发邮件检测(双触发幂等性)。

背景:GH Actions cron 不保证准点触发(高峰可被 skip)。daily.yml 设了双 cron
(7:07 / 7:23 北京)。两次都触发时,需要避免一天发两封邮件。

实现:启动时通过 GitHub Actions REST API 查询本工作流近 24 小时内是否已有
"成功"的 run。已有 → exit(0)。

环境变量(由 daily.yml 注入,本地运行时缺失,函数返回 False 不阻塞):
- GH_TOKEN:GitHub 自动生成的临时 token,只读 actions:read 权限够用
- GH_REPO:owner/repo 形如 sudsusc-cyber/daily-market-brief
- GH_RUN_ID:当前 run 的 id,排除自己
- GH_EVENT_NAME:schedule / workflow_dispatch / etc

幂等性策略:
- 仅 schedule 触发时启用幂等(workflow_dispatch 手动触发不阻挡,便于调试)
- FORCE_SEND=1 也跳过幂等检查
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from datetime import date, timezone, datetime

logger = logging.getLogger(__name__)


def _today_utc_iso() -> str:
    """UTC 当日 ISO 日期前缀,用于匹配 created_at。"""
    return datetime.now(timezone.utc).date().isoformat()


def already_sent_today() -> bool:
    """
    True = 当日已有"成功"的 schedule run(排除当前 run),应当跳过本次发送。
    False = 未发过 / 无法判定 / 本地运行 → 允许发送。

    永不抛异常:任何失败都返回 False(允许发送,以"宁可重发也不漏发"为原则的
    反面是"宁可漏发也不重发",但本函数是后者—不在双触发场景下重复)。
    """
    token = os.environ.get("GH_TOKEN")
    repo = os.environ.get("GH_REPO")
    cur_run_id_str = os.environ.get("GH_RUN_ID", "")
    event_name = os.environ.get("GH_EVENT_NAME", "")

    if not token or not repo:
        # 本地运行:不查询,允许发送
        return False
    if event_name != "schedule":
        # 手动触发(workflow_dispatch)不参与幂等
        return False

    try:
        cur_run_id = int(cur_run_id_str) if cur_run_id_str else None
    except ValueError:
        cur_run_id = None

    today = _today_utc_iso()
    url = (
        f"https://api.github.com/repos/{repo}/actions/runs"
        f"?per_page=20&event=schedule"
    )
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.load(resp)
    except Exception as exc:  # noqa: BLE001
        logger.warning("idempotency.api_failed reason=%r 允许发送", exc)
        return False

    runs = data.get("workflow_runs", []) or []
    for run in runs:
        if cur_run_id is not None and run.get("id") == cur_run_id:
            continue  # 排除自己
        if run.get("conclusion") != "success":
            continue  # 仅认成功的
        created_at = run.get("created_at", "") or ""
        if not created_at.startswith(today):
            continue  # 不是今日(UTC)
        logger.info(
            "idempotency.duplicate run_id=%s created_at=%s 已发过,跳过",
            run.get("id"), created_at,
        )
        return True
    return False
