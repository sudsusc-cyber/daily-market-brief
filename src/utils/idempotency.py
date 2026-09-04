"""
当日已发邮件检测(双触发幂等性)。

背景:外部触发器(cron-job.org)+ GH schedule 兜底,可能在同一日内多次触发
本工作流。需要避免一天发两封邮件。

实现:启动时通过 GitHub Actions REST API 查询本仓库今日(**BJT**)是否已有
"有 SMTP 接受凭证 OR 更早且正在运行" 的 run(排除当前 run)。已有 → 跳过本次。

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
- GH_RUN_ID:当前 run 的 id,排除自己

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

from src.utils.dates import BEIJING  # 统一时区源,避免每个 module 重复 ZoneInfo

logger = logging.getLogger(__name__)
_REPO_SLUG_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class IdempotencyUnavailableError(RuntimeError):
    """无法可靠判断是否已经发送；必须让当前 run 失败而不是伪装成 success。"""


def _today_beijing_iso() -> str:
    """BJT 当日 ISO 日期(YYYY-MM-DD),用于与 created_at 转 BJT 后比对。"""
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


def _run_has_successful_step(
    *, repo: str, token: str, run_id: int, step_names: set[str],
) -> bool:
    """查询 Jobs API；用于识别部分送达后至少已有 SMTP acceptance 的失败 run。"""
    url = f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/jobs?per_page=100"
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    # URL 固定为 https://api.github.com，repo 已通过 slug 白名单。
    with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310
        data = json.load(resp)
    return any(
        step.get("name") in step_names and step.get("conclusion") == "success"
        for job in (data.get("jobs", []) or [])
        for step in (job.get("steps", []) or [])
    )


def already_sent_today() -> bool:
    """
    True = 当日已有 run 在跑或已成功(排除当前 run)→ 应当跳过本次发送。
    False = 本地运行 / 确认今日无并发或成功 run → 允许发送。

    GitHub API 或关键环境异常时抛 IdempotencyUnavailableError，让 workflow 失败；
    后续串行触发可在外部状态恢复后自动重试。
    """
    token = os.environ.get("GH_TOKEN")
    repo = os.environ.get("GH_REPO")
    cur_run_id_str = os.environ.get("GH_RUN_ID", "")

    if not token or not repo:
        # 本地运行:不查询,允许发送
        return False
    if not _REPO_SLUG_RE.fullmatch(repo):
        raise IdempotencyUnavailableError("GH_REPO is not a valid owner/repo slug")

    try:
        cur_run_id = int(cur_run_id_str) if cur_run_id_str else None
    except ValueError:
        cur_run_id = None

    today = _today_beijing_iso()
    # 关键:必须按 workflow file 过滤,只查 daily.yml 的 runs。
    # 否则 monitor.yml 等其他 workflow 今天的成功 run 会被误判为"daily.yml
    # 已发过",导致 daily.yml 永远 skip 不发邮件。
    # per_page=100:24h 内 daily.yml run 通常 1-3 个,但 force_send 手测时容易
    # 短时密集触发;20 太窄,真实 run 可能被挤出页面而误判为"未发"导致重发。
    url = (
        f"https://api.github.com/repos/{repo}/actions/workflows/daily.yml/runs"
        f"?branch=main&per_page=100"
    )
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")

    try:
        # URL 固定为 https://api.github.com，repo 已通过 slug 白名单。
        with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310
            data = json.load(resp)
    except Exception as exc:  # noqa: BLE001
        logger.error("idempotency.api_failed reason=%r", exc)
        raise IdempotencyUnavailableError("GitHub Actions API unavailable") from exc

    runs = data.get("workflow_runs", []) or []

    # A successful run can be a holiday/duplicate skip, not an email delivery.
    for run in runs:
        if cur_run_id is not None and run.get("id") == cur_run_id:
            continue
        if run.get("head_branch", "main") != "main":
            continue
        if _bjt_date_of_iso(run.get("created_at") or "") != today:
            continue
        # 部分送达会让 Confirm full email delivery 失败、run 结论为 failure；
        # 但至少一位收件人已经收到，不能让后续兜底给他们重发。
        if run.get("conclusion") in {"success", "failure", "cancelled", "timed_out"} and isinstance(run.get("id"), int):
            try:
                accepted = _run_has_successful_step(
                    repo=repo,
                    token=token,
                    run_id=run["id"],
                    step_names={"Confirm SMTP acceptance", "Confirm full email delivery", "Confirm email delivery"},
                )
            except Exception as exc:  # noqa: BLE001
                logger.error("idempotency.jobs_api_failed run_id=%s reason=%r", run["id"], exc)
                raise IdempotencyUnavailableError("GitHub Jobs API unavailable") from exc
            if accepted:
                logger.warning(
                    "idempotency.partial_delivery run_id=%s → skip duplicate resend",
                    run["id"],
                )
                return True

    # 第二步:没有成功 run,但有其他 run 在 queued/in_progress —— 走 leader election。
    # 只有"自己的 run_id 是今日所有 active run 中最小(最早创建)" 才发,其他让位。
    # 这样两个并发 run 也不会"双双 skip":必有一个是最小 id → 发。
    if cur_run_id is None:
        # 异常:GH 环境通常有 GH_RUN_ID(由 daily.yml 显式注入)。能走到这里意味着
        # token / repo 都齐全但 GH_RUN_ID 缺失或非数字 — 多半是 daily.yml 改坏了。
        # 此时无法 leader election → 保守 skip(避免重发);monitor 会观察到漏发并告警。
        logger.error(
            "idempotency.no_run_id token+repo present but GH_RUN_ID missing/invalid — "
            "cannot elect leader; check daily.yml env wiring",
        )
        raise IdempotencyUnavailableError("GH_RUN_ID missing or invalid")

    active_today_ids: list[int] = [cur_run_id]
    for run in runs:
        rid = run.get("id")
        if rid is None or rid == cur_run_id:
            continue
        if run.get("head_branch", "main") != "main":
            continue
        if _bjt_date_of_iso(run.get("created_at") or "") != today:
            continue
        if run.get("status") in ("queued", "in_progress"):
            try:
                active_today_ids.append(int(rid))
            except (TypeError, ValueError):
                continue

    leader_id = min(active_today_ids)
    if cur_run_id == leader_id:
        logger.info(
            "idempotency.leader run_id=%s active_today=%s → 发",
            cur_run_id, sorted(active_today_ids),
        )
        return False
    logger.info(
        "idempotency.follower run_id=%s leader=%s → skip",
        cur_run_id, leader_id,
    )
    return True
