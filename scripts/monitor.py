"""
监控脚本:检查今天 daily.yml 是否有"成功 OR 正在跑"的 run。
都没有 → 发告警邮件给收件人。

被 .github/workflows/monitor.yml 调用。

为何也算 in_progress / queued:监控可能在 daily.yml 还没跑完时执行(GH 延迟
极端情况),不应误报。

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
from src.settings import load_settings

logger = logging.getLogger(__name__)


def check_today_status() -> tuple[bool, str]:
    """
    返回 (今日 OK?, 描述)。
    OK = 今天 UTC 有 daily.yml run 处于 success / in_progress / queued 之一。
    """
    token = os.environ.get("GH_TOKEN", "")
    repo = os.environ.get("GH_REPO", "")
    if not token or not repo:
        return False, "缺少 GH_TOKEN / GH_REPO 环境变量"

    today = datetime.now(UTC).date().isoformat()
    url = (
        f"https://api.github.com/repos/{repo}/actions/workflows/daily.yml/runs"
        f"?per_page=30"
    )
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.load(resp)
    except Exception as exc:  # noqa: BLE001
        return False, f"GH API 调用失败: {exc!r}"

    runs = data.get("workflow_runs", []) or []
    today_runs = [r for r in runs if (r.get("created_at") or "").startswith(today)]

    if not today_runs:
        return False, f"今日({today} UTC)无 daily.yml run 记录"

    for r in today_runs:
        if r.get("conclusion") == "success":
            return True, (
                f"今日已成功 run_id={r.get('id')} "
                f"created_at={r.get('created_at')}"
            )
        if r.get("status") in ("queued", "in_progress"):
            return True, (
                f"今日有 run 进行中 run_id={r.get('id')} "
                f"status={r.get('status')}"
            )

    # 今日有 run,但全是 failure / cancelled
    failed_ids = [r.get("id") for r in today_runs]
    return False, f"今日所有 run 都失败/取消: ids={failed_ids}"


def send_alert(reason: str) -> None:
    """发告警邮件。失败抛异常(让 monitor 这个 run 报 failure,GH 也会通知)。"""
    settings = load_settings()
    recipients = [r.strip() for r in settings.email_recipient.split(",") if r.strip()]
    repo = os.environ.get("GH_REPO", "")
    actions_url = f"https://github.com/{repo}/actions" if repo else "GitHub Actions"
    cronjob_url = "https://console.cron-job.org/jobs"

    subject = "⚠️ 朝闻录监控告警 — 今日邮件可能未发出"
    body = f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 640px; margin: 0 auto; padding: 24px; color: #1a1a1a;">
      <h2 style="color: #c0392b; margin-top: 0;">⚠️ 朝闻录监控告警</h2>
      <p><strong>检测时间:</strong>{datetime.now(UTC).isoformat(timespec='seconds')} UTC</p>
      <p><strong>原因:</strong>{reason}</p>

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
    send_alert(reason)
    logger.info("monitor.alert_sent")
    # 监控触发告警时,本 run 仍以 success 退出(避免 GH 重复告警)
    return 0


if __name__ == "__main__":
    sys.exit(main())
