"""
当日已发邮件检测(双触发幂等性)。

背景:外部触发器(cron-job.org)+ GH schedule 兜底,可能在同一日内多次触发
本工作流。需要避免一天发两封邮件。

实现:启动时通过 GitHub Actions REST API 查询本仓库今日(UTC)是否已有
"成功 OR 正在运行" 的 run(排除当前 run)。已有 → exit(0) 跳过本次。

为何也算 in-progress / queued:
  避免 TOCTOU 竞态。例如 cron-job.org 7:00 和 GH schedule(延迟到 ~7:00)同时
  触发,两个 run 都在 ~7:00:30 调用本函数,这时谁都还没完成("success" 都
  没出现),只看 conclusion=success 就会漏判 → 双发。把 in_progress / queued
  也算入,先到的 run 让后到的看到"已经在跑"→ skip。

环境变量(由 daily.yml 注入,本地运行时缺失,函数返回 False 不阻塞):
- GH_TOKEN:GitHub 自动生成的临时 token,只读 actions:read 权限够用
- GH_REPO:owner/repo 形如 sudsusc-cyber/daily-market-brief
- GH_RUN_ID:当前 run 的 id,排除自己

幂等性策略:
- 所有触发类型(schedule / workflow_dispatch / repository_dispatch)统一参与幂等
- 状态判断:conclusion=success(已完成)OR status in (queued, in_progress)
- API 调用失败 → fail-close(返回 True,跳过本次发送)
  设计取舍:GH API 偶发抖动时,宁可漏发不重发(漏发用户会察觉,重发更打扰)
- FORCE_SEND=true 或 FORCE_SEND=1 在 main.py 层跳过本检查(人工强制重发)
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
    True = 当日已有 run 在跑或已成功(排除当前 run)→ 应当跳过本次发送。
    False = 本地运行 / 确认今日无并发或成功 run → 允许发送。

    永不抛异常。fail-close 默认:API 失败时返回 True(保守,宁可漏不重)。
    """
    token = os.environ.get("GH_TOKEN")
    repo = os.environ.get("GH_REPO")
    cur_run_id_str = os.environ.get("GH_RUN_ID", "")

    if not token or not repo:
        # 本地运行:不查询,允许发送
        return False

    try:
        cur_run_id = int(cur_run_id_str) if cur_run_id_str else None
    except ValueError:
        cur_run_id = None

    today = _today_utc_iso()
    url = (
        f"https://api.github.com/repos/{repo}/actions/runs"
        f"?per_page=20"
    )
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.load(resp)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "idempotency.api_failed reason=%r fail-close → 跳过本次发送(保守)", exc,
        )
        return True  # fail-close:API 失败 → 保守跳过,避免 GH API 抖动时双发

    runs = data.get("workflow_runs", []) or []
    for run in runs:
        if cur_run_id is not None and run.get("id") == cur_run_id:
            continue  # 排除自己
        created_at = run.get("created_at", "") or ""
        if not created_at.startswith(today):
            continue  # 不是今日(UTC)
        status = run.get("status")
        conclusion = run.get("conclusion")
        # 已成功 OR 正在排队 / 正在跑 → 都视为"今天已经在处理了"
        if conclusion == "success" or status in ("queued", "in_progress"):
            logger.info(
                "idempotency.duplicate run_id=%s status=%s conclusion=%s "
                "created_at=%s 已发过或正在跑,跳过",
                run.get("id"), status, conclusion, created_at,
            )
            return True
    return False
