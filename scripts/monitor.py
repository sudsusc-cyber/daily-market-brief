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
import re
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

# 让脚本能找到 src/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.sender.smtp_sender import send_html_email
from src.settings import load_email_settings
from src.utils.action_evidence import bjt_date, candidate_run, pages, run_evidence, workflow_runs
from src.utils.dates import BEIJING
from src.utils.holidays import should_send_today

logger = logging.getLogger(__name__)

_ALERT_PATH_ENV = "MONITOR_ALERT_PATH"
_REPO_SLUG_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


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


def _github_json(url: str, token: str) -> dict | list:
    """调用 GitHub REST API 并返回 JSON。"""
    if not url.startswith("https://api.github.com/"):
        raise ValueError("GitHub API URL must use the official HTTPS endpoint")
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    with urllib.request.urlopen(req, timeout=15) as resp:  # nosec B310
        return json.load(resp)


def _run_has_delivery_confirmation(*, repo: str, token: str, run_id: int) -> bool:
    """Jobs API 中存在成功的 Confirm email delivery 步骤才算实际送达。"""
    return run_evidence(lambda url: _github_json(url, token), repo, run_id,
                        today=_today_beijing_iso())["full"]


def _quality_annotation_details(repo: str, token: str, run_id: int, today: str) -> str:
    """Read only our quality annotation from the job for the affected edition.

    Diagnostics failure must never erase confirmed SMTP acceptance. Bound pages
    and text; never embed arbitrary workflow log output in a recipient email.
    """
    try:
        jobs = pages(lambda url: _github_json(url, token),
                     f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/jobs?filter=all&per_page=100", "jobs")
        messages = []
        for job in jobs:
            relevant = any(step.get('conclusion') == 'success' and
                           step.get('name', '').partition(' | ')[0] == 'Report content quality warning' and
                           (step['name'].partition(' | ')[2] or bjt_date(step.get('completed_at'))) == today
                           for step in job.get('steps', []))
            if not relevant:
                continue
            check_url = job.get('check_run_url', '')
            if not re.fullmatch(re.escape(f'https://api.github.com/repos/{repo}/check-runs/') + r'\d+', check_url):
                continue
            for page in range(1, 11):
                rows = _github_json(f'{check_url}/annotations?per_page=100&page={page}', token)
                if not isinstance(rows, list):
                    raise ValueError('invalid annotation response')
                for row in rows:
                    if row.get('title') == 'Daily brief content quality':
                        message = str(row.get('message', ''))[:6000]
                        message = re.sub(r'[\w.+-]+@[\w.-]+', '[email]', message)
                        messages.append(message)
                if len(rows) < 100:
                    break
        return '\n'.join(dict.fromkeys(messages))[:6000] or '质量明细暂不可读取，请查看本次运行归档。'
    except Exception as exc:
        logger.warning('monitor.quality_details_unavailable type=%s', type(exc).__name__)
        return '质量明细读取失败；SMTP 已接受的结论不变，请查看本次运行归档。'


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
    if not _REPO_SLUG_RE.fullmatch(repo):
        return False, "GH_REPO 不是合法的 owner/repo"

    today = _today_beijing_iso()
    try:
        runs = list(workflow_runs(lambda url: _github_json(url, token), repo))
    except Exception as exc:
        return False, f"GH API 调用失败: {type(exc).__name__}"
    today_runs = [run for run in runs if candidate_run(run, today)]

    if not today_runs:
        return False, f"今日({today} BJT)无 daily.yml run 记录"

    job_lookup_errors: list[str] = []
    for run in today_runs:
        run_id = run.get("id")
        if not isinstance(run_id, int):
            continue
        try:
            evidence = run_evidence(lambda url: _github_json(url, token), repo, run_id, today=today)
            if evidence["accepted"]:
                if not evidence["full"]:
                    return False, f"SMTP 部分接受 run_id={run_id}；禁止自动整封重发，请核对拒收状态"
                if evidence["degraded"]:
                    details = _quality_annotation_details(repo, token, run_id, today)
                    return False, (f"邮件已全体 SMTP 接受，但内容降级 run_id={run_id}；仅质量告警，禁止整封重发\n"
                                   f"运行：https://github.com/{repo}/actions/runs/{run_id}\n{details}")
                return True, f"今日邮件已确认送达 run_id={run_id} edition={today}"

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

    quality_only = reason.startswith("邮件已全体 SMTP 接受，但内容降级")
    subject = "⚠️ 朝闻录内容质量提醒 — 邮件已发送" if quality_only else "⚠️ 朝闻录监控告警 — 投递异常"
    heading = "内容质量提醒（邮件已发送）" if quality_only else "监控告警"
    body = f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 640px; margin: 0 auto; padding: 24px; color: #1a1a1a;">
      <h2 style="color: #c0392b; margin-top: 0;">⚠️ 朝闻录{heading}</h2>
      <p><strong>检测时间:</strong>{datetime.now(BEIJING).strftime('%Y-%m-%d %H:%M:%S')} 北京时间</p>
      <p style="white-space:pre-line"><strong>原因:</strong>{safe_reason}</p>

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

    if quality_only:
        start = body.index('      <p><strong>排查步骤:')
        end = body.index('      <p style="color: #888;', start)
        body = body[:start] + '<p>邮件已获得 SMTP 接受。请按上述有效沿用、加工状态及摘要形式分别检查内容；不要因此重新发送整封邮件。</p>' + body[end:]
    result = send_html_email(
        sender=settings.qq_email_address,
        sender_display_name="朝闻录监控",
        auth_code=settings.qq_email_auth_code,
        recipient=recipients,
        subject=subject,
        html_body=body,
    )
    if result.refused:
        raise RuntimeError(
            f"Monitor alert incomplete: accepted={len(result.accepted)} refused={len(result.refused)}"
        )


def _alert_already_sent_today() -> bool:
    """已有 monitor run 成功执行告警步骤时跳过第二封重复告警。"""
    token = os.environ.get("GH_TOKEN", "")
    repo = os.environ.get("GH_REPO", "")
    current_run = os.environ.get("GH_RUN_ID", "")
    if not token or not repo:
        return False
    if not _REPO_SLUG_RE.fullmatch(repo):
        logger.warning("monitor.alert_dedup_invalid_repo")
        return False
    try:
        def get_json(url):
            return _github_json(url, token)
        for run in workflow_runs(get_json, repo, "monitor.yml"):
            if not candidate_run(run, _today_beijing_iso()):
                continue
            rid = run.get("id")
            current_attempt = int(os.environ.get("GH_RUN_ATTEMPT", "1"))
            if str(rid) == current_run and current_attempt == 1:
                continue
            if run_evidence(get_json, repo, rid, today=_today_beijing_iso(),
                            exclude_attempt=current_attempt if str(rid) == current_run else None)["alerted"]:
                return True

    except Exception as exc:  # noqa: BLE001
        # 去重检查失败不能吞掉真正告警；最多承担重复一封的较小风险。
        logger.warning("monitor.alert_dedup_failed exc_type=%s", type(exc).__name__)
    return False


def _alert_request_path() -> Path:
    raw = os.environ.get(_ALERT_PATH_ENV, ".monitor-alert.txt").strip()
    return Path(raw or ".monitor-alert.txt")


def check_only() -> int:
    """工作流第一步：只检查并把告警原因写入临时文件。"""
    path = _alert_request_path()
    path.unlink(missing_ok=True)
    ok, reason = check_today_status()
    logger.info("monitor.check ok=%s reason=%s", ok, reason)
    if not ok:
        path.write_text(reason, encoding="utf-8")
    return 0


def send_requested_alert() -> int:
    """工作流第二步：若当日尚未告警，则发送临时文件中的原因。"""
    path = _alert_request_path()
    reason = path.read_text(encoding="utf-8").strip()
    if _alert_already_sent_today():
        logger.info("monitor.alert_skipped reason=今日已有成功告警")
        return 0
    send_alert(reason)
    logger.info("monitor.alert_sent")
    return 0


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
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    if "--check-only" in sys.argv:
        sys.exit(check_only())
    if "--send-request" in sys.argv:
        sys.exit(send_requested_alert())
    sys.exit(main())
