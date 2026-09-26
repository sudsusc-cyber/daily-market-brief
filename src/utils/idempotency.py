"""
当日已发邮件检测(双触发幂等性)。

背景:外部触发器(cron-job.org)+ GH schedule 兜底,可能在同一日内多次触发
本工作流。需要避免一天发两封邮件。

实现:启动时通过 GitHub Actions REST API 查询本仓库今日(**BJT**)是否已有
"有 SMTP 接受凭证 OR 更早且正在运行" 的执行(仅排除当前 attempt)。已有 → 跳过本次。

TODO(audit-2026-05-04 #B1): leader 失败 + follower 后保存时,cache 会回退一天 state。
monitor.yml 能捕获 leader 失败并告警,手动 force_send 后自动恢复。
修复方案见 docs/audits/2026-05-04-audit.md B1 节(follower 不写 cache)。
仅在 monitor 真观察到 "leader 失败 + state 倒退" 时再实施。

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

Leader election(防双 fail):
  上面的"看到 in_progress 就 skip"在两个 run 几乎同秒启动时会"双双 skip" ——
  Run A 看到 Run B in_progress → skip;Run B 看到 Run A in_progress → skip;
  没人发邮件 + monitor 又看到两个 run "成功"(其实都 exit 0),静默漏发。
  解法:run_id 比较 leader election —— 只在自己的 run_id 是当日最小(最早创建)
  时才算 leader,其他 run 都让位。GH 的 run_id 是单调递增的全局序号,可作为
  确定性的"先到"判定。如果 leader 真挂了(crash 在 already_sent_today 之后),
  monitor 会在 BJT 08:30 检测出"今日无成功 run"告警 — 不会静默。

环境变量(由 daily.yml 注入,本地运行时缺失,函数返回 False 不阻塞):
- GH_TOKEN:GitHub 自动生成的临时 token,只读 actions:read 权限够用
- GH_REPO:owner/repo 形如 sudsusc-cyber/daily-market-brief
- GH_RUN_ID:当前 run 的 id
- GH_RUN_ATTEMPT:仅排除此 attempt，历史 attempts 仍检查

幂等性策略:
- 所有触发类型(schedule / workflow_dispatch / repository_dispatch)统一参与幂等
- 已结束的 run 必须有送达确认步骤；单纯 success 可能只是休市或重复触发跳过。
- 尚在 queued / in_progress 的 run 按最小 run_id 选出唯一发送者。
  - API 调用失败 → 抛 IdempotencyUnavailableError,让 run 失败
    后续串行 follower 在 API 恢复后可自动重试,避免 poison success。
- FORCE_SEND=true 或 FORCE_SEND=1 在 main.py 层跳过本检查(人工强制重发)
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.request
from datetime import datetime

from src.utils.action_evidence import candidate_run, run_evidence, workflow_runs
from src.utils.dates import BEIJING  # 统一时区源,避免每个 module 重复 ZoneInfo

logger = logging.getLogger(__name__)
_REPO_SLUG_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class IdempotencyUnavailableError(RuntimeError):
    """无法可靠判断是否已经发送；必须让当前 run 失败而不是伪装成 success。"""


def _today_beijing_iso() -> str:
    """BJT 当日 ISO 日期(YYYY-MM-DD),用于与实际 SMTP 确认版次比对。"""
    return datetime.now(BEIJING).date().isoformat()


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
    return dt.astimezone(BEIJING).date().isoformat()


def _github_json(url: str, token: str) -> dict:
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310
        return json.load(resp)


def already_sent_today() -> bool:
    """Fail closed if the API cannot prove that another execution has not sent."""
    token, repo = os.environ.get("GH_TOKEN"), os.environ.get("GH_REPO")
    if not token or not repo:
        return False
    if not _REPO_SLUG_RE.fullmatch(repo):
        raise IdempotencyUnavailableError("GH_REPO is not a valid owner/repo slug")
    try:
        current = int(os.environ.get("GH_RUN_ID", ""))
        attempt = int(os.environ.get("GH_RUN_ATTEMPT", os.environ.get("GITHUB_RUN_ATTEMPT", "1")))
        if current < 1 or attempt < 1:
            raise ValueError("invalid execution")
    except ValueError as exc:
        raise IdempotencyUnavailableError("GH_RUN_ID/attempt missing or invalid") from exc
    today = _today_beijing_iso()
    def get_json(url):
        return _github_json(url, token)
    try:
        runs = list(workflow_runs(get_json, repo))
        # Always examine our own old attempts, including when a run listing
        # page omitted the current in-progress execution.
        candidates = {run["id"]: run for run in runs if candidate_run(run, today)}
        if attempt > 1:
            candidates.setdefault(current, {"id": current})
        for rid in candidates:
            if rid == current and attempt == 1:
                continue
            evidence = run_evidence(get_json, repo, rid, today=today,
                                    exclude_attempt=attempt if rid == current else None)
            if evidence["accepted"]:
                logger.warning("idempotency.smtp_accepted run_id=%s edition=%s", rid, today)
                return True
        active = [run["id"] for run in runs if candidate_run(run, today)
                  and run.get("status") in {"queued", "in_progress"}]
        return current != min([current, *active])
    except Exception as exc:
        raise IdempotencyUnavailableError("GitHub delivery evidence unavailable") from exc
