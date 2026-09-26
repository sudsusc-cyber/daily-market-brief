"""Shared, paginated Actions evidence for delivery and monitoring.

A run can be rerun months after creation. Edition identity belongs to the
SMTP receipt/confirmation step, never to the run's original created_at.
"""

from datetime import date, datetime

from src.utils.dates import BEIJING

ACCEPTED_STEPS = {
    "Confirm SMTP acceptance",
    "Confirm full email delivery",
    "Confirm email delivery",
}
FULL_STEPS = {"Confirm full email delivery", "Confirm email delivery"}


def bjt_date(value: str) -> str | None:
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            return None
        return stamp.astimezone(BEIJING).date().isoformat()
    except (ValueError, TypeError, AttributeError):
        return None


def pages(get_json, url: str, key: str):
    for page in range(1, 1001):
        data = get_json(f"{url}&page={page}")
        rows = data.get(key)
        if not isinstance(rows, list):
            raise ValueError(f"Actions response missing {key}")
        yield from rows
        if len(rows) < 100:
            return
    raise RuntimeError("Actions pagination exceeded safe limit")


def workflow_runs(get_json, repo: str, workflow: str = "daily.yml"):
    return pages(
        get_json,
        f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/runs"
        "?branch=main&per_page=100",
        "workflow_runs",
    )


def candidate_run(run: dict, today: str) -> bool:
    # updated_at catches old run IDs rerun for today's edition.
    return run.get("head_branch", "main") == "main" and any(
        (bjt_date(run.get(key)) or "") >= today
        for key in ("created_at", "updated_at", "run_started_at")
    )


def run_evidence(
    get_json, repo: str, run_id: int, *, today: str, exclude_attempt: int | None = None
) -> dict[str, bool]:
    result = {"accepted": False, "full": False, "degraded": False, "alerted": False}
    jobs = pages(
        get_json,
        f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/jobs"
        "?filter=all&per_page=100",
        "jobs",
    )
    for job in jobs:
        attempt = job.get("run_attempt")
        if exclude_attempt is not None:
            if type(attempt) is not int:
                raise ValueError(
                    "Jobs API missing run_attempt; cannot safely exclude current execution"
                )
            if attempt == exclude_attempt:
                continue
        for step in job.get("steps", []) or []:
            if step.get("conclusion") != "success":
                continue
            name = step.get("name", "")
            # New workflows bind the actual receipt edition into the step name.
            # Legacy confirmation steps use their own completion timestamp.
            base, separator, edition = name.partition(" | ")
            observed = edition if separator else bjt_date(step.get("completed_at"))
            if base in ACCEPTED_STEPS:
                try:
                    date.fromisoformat(observed or "")
                except ValueError as exc:
                    raise ValueError("SMTP acceptance evidence has no valid edition") from exc
            if observed != today:
                continue
            result["accepted"] |= base in ACCEPTED_STEPS
            result["full"] |= base in FULL_STEPS
            result["degraded"] |= base == "Report content quality warning"
            result["alerted"] |= base == "Send monitor alert"
    return result
