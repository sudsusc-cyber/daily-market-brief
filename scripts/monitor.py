"""
监控脚本:检查今天 daily.yml 是否有明确的 SMTP 送达确认步骤。
没有送达确认 → 发告警邮件给收件人。

被 .github/workflows/monitor.yml 调用。

监控在主发送窗口约两小时后运行；届时仍处于 in_progress / queued 且没有送达确认，
也属于需要告警的异常状态。

为何用 GITHUB_TOKEN 而非 PAT:监控自身用 GH Actions 内置 token,与外部 PAT
解耦,即使 PAT 过期监控仍工作。

环境变量(由 monitor.yml 注入):
- GH_TOKEN:GITHUB_TOKEN(actions:read)
- GH_REPO:owner/repo
- QQ_EMAIL_ADDRESS / QQ_EMAIL_AUTH_CODE / EMAIL_RECIPIENT:发告警邮件
"""

from __future__ import annotations

import json
import logging
import os
import sys
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

# 让脚本能找到 src/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.sender.smtp_sender import send_html_email
from src.settings import load_email_settings
from src.utils.dates import BEIJING
from src.utils.holidays import should_send_today

logger = logging.getLogger(__name__)


def _today_beijing_iso() -> str:
    return datetime.now(BEIJING).date().isoformat()


def _bjt_date_of_iso(iso_str: str) -> str | None:
    """把 GH API 的 UTC ISO 字符串(如 '2026-05-03T22:30:15Z')转 BJT 日期字符串。"""
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return dt.astimezone(BEIJING).date().isoformat()


def _github_json(url: str, token: str) -> dict:
    """调用 GitHub REST API 并返回 JSON。"""
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.load(resp)


def _run_has_delivery_confirmation(*, repo: str, token: str, run_id: int) -> bool:
    """Jobs API 中存在成功的 Confirm email delivery 步骤才算实际送达。"""
    url = f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/jobs?per_page=100"
    data = _github_json(url, token)
    return any(
        step.get("name") == "Confirm email delivery" and step.get("conclusion") == "success"
        for job in (data.get("jobs", []) or [])
        for step in (job.get("steps", []) or [])
    )


def check_today_status() -> tuple[bool, str]:
    """
    返回 (今日 OK?, 描述)。
    OK = 今日无需发送（美股休市），或 daily.yml 有明确的邮件送达确认步骤。

    必须用 BJT date 比对(与 idempotency.already_sent_today 同口径):
      cron 在 BJT 06:30 触发 = UTC 22:30(前一日)。monitor 在 BJT 08:30
      = UTC 00:30 检查时,daily 的 created_at 是前一日 UTC string。用 UTC
      比对会判"今日无 run"误报告警。
    """
    send_expected, market_reason = should_send_today(datetime.now(BEIJING).date())
    if not send_expected:
        return True, f"今日无需发送: {market_reason}"

    token = os.environ.get("GH_TOKEN", "")
    repo = os.environ.get("GH_REPO", "")
    if not token or not repo:
        return False, "缺少 GH_TOKEN / GH_REPO 环境变量"

    today = _today_beijing_iso()
    url = (
        f"https://api.github.com/repos/{repo}/actions/workflows/daily.yml/runs"
        f"?per_page=30"
    )
    try:
        data = _github_json(url, token)
    except Exception as exc:  # noqa: BLE001
        return False, f"GH API 调用失败: {exc!r}"

    runs = data.get("workflow_runs", []) or []
    today_runs = [r for r in runs if _bjt_date_of_iso(r.get("created_at") or "") == today]

    if not today_runs:
        return False, f"今日({today} BJT)无 daily.yml run 记录"

    job_lookup_errors: list[str] = []
    for run in today_runs:
        run_id = run.get("id")
        if not isinstance(run_id, int):
            continue
        try:
            if _run_has_delivery_confirmation(repo=repo, token=token, run_id=run_id):
                return True, (
                    f"今日邮件已确认送达 run_id={run_id} "
                    f"created_at={run.get('created_at')}"
                )
        except Exception as exc:  # noqa: BLE001
            job_lookup_errors.append(f"run_id={run_id}: {type(exc).__name__}: {exc}")

    if job_lookup_errors:
        return False, f"无法核验邮件送达步骤: {'; '.join(job_lookup_errors)}"

    active_ids = [
        r.get("id") for r in today_runs if r.get("status") in ("queued", "in_progress")
    ]
    if active_ids:
        return False, f"今日 run 仍未完成且无送达确认: ids={active_ids}"

    run_ids = [r.get("id") for r in today_runs]
    return False, f"今日 run 均无邮件送达确认: ids={run_ids}"


def send_alert(reason: str) -> None:
    """发告警邮件。失败抛异常(让 monitor 这个 run 报 failure,GH 也会通知)。

    reason / repo 都做 HTML escape:reason 来自 check_today_status,可能含异常 repr,
    若异常 message 含 < > & " 字符,直接拼 HTML 会显示错乱(极端情况 注入)。
    """
    import html as _html

    settings = load_email_settings()
    recipients = [r.strip() for r in settings.email_recipient.split(",") if r.strip()]
    repo = os.environ.get("GH_REPO", "")
    actions_url = (
        f"https://github.com/{_html.escape(repo, quote=True)}/actions"
        if repo else "https://github.com/"
    )
    cronjob_url = "https://console.cron-job.org/jobs"
    safe_reason = _html.escape(reason or "未知原因")

    subject = "⚠️ 朝闻录监控告警 — 今日邮件可能未发出"
    body = f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 640px; margin: 0 auto; padding: 24px; color: #1a1a1a;">
      <h2 style="color: #c0392b; margin-top: 0;">⚠️ 朝闻录监控告警</h2>
      <p><strong>检测时间:</strong>{datetime.now(UTC).isoformat(timespec='seconds')} UTC</p>
      <p><strong>原因:</strong>{safe_reason}</p>

      <hr style="border: 0; border-top: 1px solid #e0e0e0; margin: 24px 0;">

      <p><strong>排查步骤:</strong></p>
      <ol>
        <li>打开 <a href="{actions_url}">GitHub Actions 页面</a> 看今天有没有 daily.yml run</li>
        <li>如果有 run 但失败,点进去看错误</li>
        <li>如果完全没有 run,打开 <a href="{cronjob_url}">cron-job.org 任务历史</a>:
          <ul>
            <li>HTTP 401 → PAT 过期,去 GitHub 重生成并更新 cron-job.org Authorization header</li>
            <li>HTTP 5xx → GitHub API 抖动,等等再看</li>
            <li>没有触发记录 → cron-job.org 任务被禁用了</li>
          </ul>
        </li>
        <li>如果 daily.yml 跑了但你没收到,检查 QQ 邮箱垃圾箱、SMTP 授权码是否还有效</li>
      </ol>

      <p style="color: #888; font-size: 12px; margin-top: 32px;">
        本邮件由监控脚本 scripts/monitor.py 自动发出。
        如不希望再收到,在 cron-job.org 关闭"朝闻录监控"任务。
      </p>
    </div>
    """

    send_html_email(
        sender=settings.qq_email_address,
        sender_display_name="朝闻录监控",
        auth_code=settings.qq_email_auth_code,
        recipient=recipients,
        subject=subject,
        html_body=body,
    )


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    ok, reason = check_today_status()
    logger.info("monitor.check ok=%s reason=%s", ok, reason)

    if ok:
        return 0

    logger.warning("monitor.alert reason=%s", reason)
    # 发送失败必须向上抛，让 GitHub Actions 以 failure 作为独立备用告警通道。
    send_alert(reason)
    logger.info("monitor.alert_sent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
