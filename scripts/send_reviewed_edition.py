"""Send an explicitly reviewed, hash-bound preview without regenerating content."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from src.sender.smtp_sender import InlineImage, _html_to_plain, send_html_email
from src.utils.brief_audit import archive_delivery, archive_publication
from src.utils.dates import BEIJING
from src.utils.delivery import clear_delivery_receipt, write_delivery_receipt


def gh_json(endpoint: str, *, pages: bool = False):
    command = ["gh", "api", endpoint]
    if pages:
        command += ["--paginate", "--slurp"]
    return json.loads(subprocess.check_output(command, text=True))  # nosec B603


def validate_run(run: dict, expected_sha: str) -> None:
    if (
        run.get("head_sha") != expected_sha
        or run.get("head_branch") != "main"
        or run.get("path") != ".github/workflows/formal-test-send.yml"
        or run.get("event") != "workflow_dispatch"
        or run.get("status") != "completed"
        or run.get("conclusion") != "success"
    ):
        raise ValueError("preview must be a successful main workflow at the current commit")


def load_reviewed(directory: Path, *, digest: str, run_id: str, attempt: str, now: datetime):
    manifest = json.loads((directory / "manifest.json").read_text())
    if str(manifest["run_id"]) != run_id or str(manifest["run_attempt"]) != attempt:
        raise ValueError("preview identity mismatch")
    if manifest.get("delivery", {}).get("status") != "not_sent":
        raise ValueError("artifact is not an unsent preview")
    generated = datetime.fromisoformat(manifest["generated_at"])
    if (
        generated.tzinfo is None
        or not timedelta(0) <= now - generated <= timedelta(hours=1)
        or generated.astimezone(BEIJING).date() != now.astimezone(BEIJING).date()
    ):
        raise ValueError("reviewed preview expired or edition changed")
    if not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise ValueError("invalid reviewed digest")
    html = (directory / "email.html").read_text()
    plain = (directory / "email.txt").read_text()
    if hashlib.sha256(html.encode()).hexdigest() != digest or manifest["sha256"]["html"] != digest:
        raise ValueError("reviewed HTML hash mismatch")
    if hashlib.sha256(plain.encode()).hexdigest() != manifest["sha256"][
        "text"
    ] or plain != _html_to_plain(html):
        raise ValueError("plain text mismatch")
    subject = manifest.get("subject", "")
    if not subject or "\r" in subject or "\n" in subject:
        raise ValueError("invalid reviewed subject")
    images = []
    seen = set()
    for row in manifest["images"]:
        path = directory / row["path"]
        if not path.resolve().is_relative_to(directory.resolve()) or path.is_symlink():
            raise ValueError("invalid image path")
        if hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]:
            raise ValueError("reviewed image hash mismatch")
        if row["cid"] in seen or re.search(r"[\r\n<>]", row["cid"]):
            raise ValueError("invalid image CID")
        seen.add(row["cid"])
        images.append(InlineImage(cid=row["cid"], path=path))
    if set(re.findall(r'cid:([^"\s<>]+)', html)) - seen:
        raise ValueError("missing reviewed image")
    return manifest, html, images


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    clear_delivery_receipt()
    repo = os.environ["GITHUB_REPOSITORY"]
    run_id = os.environ["REVIEWED_RUN_ID"]
    if not run_id.isdigit():
        raise ValueError("invalid reviewed run ID")
    run = gh_json(f"repos/{repo}/actions/runs/{run_id}")
    validate_run(run, os.environ["GITHUB_SHA"])
    # Re-running the same dispatch may not resend after any recipient accepted.
    pages = gh_json(
        f"repos/{repo}/actions/runs/{os.environ['GITHUB_RUN_ID']}/jobs?filter=all&per_page=100",
        pages=True,
    )
    if any(
        step.get("name") == "Confirm SMTP acceptance" and step.get("conclusion") == "success"
        for page in pages
        for job in page["jobs"]
        for step in job.get("steps", [])
    ):
        raise ValueError("this run already has SMTP acceptance in a previous attempt")
    attempt = str(run["run_attempt"])
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        subprocess.run(
            [
                "gh",
                "run",
                "download",
                run_id,
                "--repo",
                repo,
                "--name",
                f"brief-{run_id}-{attempt}",
                "--dir",
                temporary,
            ],
            check=True,
        )  # nosec B603
        candidates = list(root.glob("*/manifest.json"))
        if len(candidates) != 1:
            raise ValueError("expected one reviewed edition")
        manifest, html, images = load_reviewed(
            candidates[0].parent,
            digest=os.environ["REVIEWED_HTML_SHA256"],
            run_id=run_id,
            attempt=attempt,
            now=datetime.now(BEIJING),
        )
        report = {
            **manifest["content"],
            "reviewed_from": {
                "run_id": run_id,
                "attempt": attempt,
                "commit": run["head_sha"],
                "html_sha256": manifest["sha256"]["html"],
            },
        }
        directory = archive_publication(
            html,
            generated_at=datetime.fromisoformat(manifest["generated_at"]),
            report=report,
            inline_images=images,
            subject=manifest["subject"],
        )

        def accepted(result):
            nonlocal directory
            if not result.accepted:
                return
            now = datetime.now(BEIJING)
            write_delivery_receipt(
                sent_at=now,
                accepted_count=len(result.accepted),
                refused_count=len(result.refused),
                run_id=os.environ["GITHUB_RUN_ID"],
            )
            directory = archive_delivery(
                directory,
                {
                    "status": "partial" if result.refused else "full",
                    "accepted_count": len(result.accepted),
                    "refused_count": len(result.refused),
                    "sent_at": now.isoformat(),
                    "edition": now.date().isoformat(),
                },
            )

        result = send_html_email(
            sender=os.environ["QQ_EMAIL_ADDRESS"],
            auth_code=os.environ["QQ_EMAIL_AUTH_CODE"],
            recipient=[s.strip() for s in os.environ["EMAIL_RECIPIENT"].split(",") if s.strip()],
            sender_display_name="每日期刊",
            subject=manifest["subject"],
            html_body=html,
            inline_images=images,
            on_progress=accepted,
        )
        accepted(result)


if __name__ == "__main__":
    main()
