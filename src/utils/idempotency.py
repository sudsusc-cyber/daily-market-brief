"""
当日已发邮件检测(双触发幂等性)。

背景:外部触发器(cron-job.org)+ GH schedule 兜底,可能在同一日内多次触发
本工作流。需要避免一天发两封邮件。

实现:启动时通过 GitHub Actions REST API 查询本仓库今日(**BJT**)是否已有
"成功 OR 正在运行" 的 run(排除当前 run)。已有 → exit(0) 跳过本次。

为何用 BJT 而非 UTC 比较:
  cron 触发时 BJT 06:30 = UTC 22:30(前一日)。如果用 UTC 日期作为"今日",
  cron-job.org 在 22:59:50 启动 + GH schedule 在 23:00:10 启动这种场景
  会因跨过 UTC 0 点而被判为"不同日"→ 双发。改用 BJT date 与触发语义对齐
  ("BJT Tue-Sat 06:30 发一封"),边界稳定。

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
from datetime import datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_BJT = ZoneInfo("Asia/Shanghai")


def _today_beijing_iso() -> str:
    """BJT 当日 ISO 日期(YYYY-MM-DD),用于与 created_at 转 BJT 后比对。"""
    return datetime.now(_BJT).date().isoformat()


def _bjt_date_of_iso(iso_str: str) -> str | None:
    """把 GH API 的 UTC ISO 字符串(如 '2026-05-03T22:30:15Z')转 BJT 日期字符串。

    解析失败返回 None,调用方应跳过该 run。
    """
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return dt.astimezone(_BJT).date().isoformat()


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

    today = _today_beijing_iso()
    # 关键:必须按 workflow file 过滤,只查 daily.yml 的 runs。
    # 否则 monitor.yml 等其他 workflow 今天的成功 run 会被误判为"daily.yml
    # 已发过",导致 daily.yml 永远 skip 不发邮件。
    url = (
        f"https://api.github.com/repos/{repo}/actions/workflows/daily.yml/runs"
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
        run_bjt_date = _bjt_date_of_iso(created_at)
        if run_bjt_date != today:
            continue  # 不是今日(BJT)
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
