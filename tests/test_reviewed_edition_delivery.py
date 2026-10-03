import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import send_reviewed_edition as send
from src.sender.smtp_sender import DeliveryResult
from src.utils.brief_audit import archive_publication
from src.utils.dates import BEIJING

NOW = datetime(2026, 10, 3, 21, 30, tzinfo=BEIJING)


def preview(tmp_path, monkeypatch):
    monkeypatch.setenv("BRIEF_AUDIT_DIR", str(tmp_path / "preview"))
    monkeypatch.setenv("GH_RUN_ID", "123")
    monkeypatch.setenv("GH_RUN_ATTEMPT", "1")
    image = tmp_path / "image.png"
    image.write_bytes(b"preview bytes")
    html = '<p>经核验正文。</p><img src="cid:header">'
    folder = archive_publication(
        html,
        generated_at=NOW,
        report={"status": "verified", "counts": {}, "news_coverage": {}},
        inline_images=[SimpleNamespace(cid="header", path=image)],
        subject="正式期刊",
    )
    return folder, hashlib.sha256(html.encode()).hexdigest(), html


def test_exact_html_images_and_subject_survive_review(tmp_path, monkeypatch):
    folder, digest, html = preview(tmp_path, monkeypatch)
    manifest, body, images = send.load_reviewed(
        folder, digest=digest, run_id="123", attempt="1", now=NOW
    )
    assert body == html and manifest["subject"] == "正式期刊"
    assert images[0].path.read_bytes() == b"preview bytes"


@pytest.mark.parametrize(
    "bad", ["html", "text", "image", "expired", "future", "sent", "run", "hash"]
)
def test_unreviewed_or_stale_artifact_fails_closed(tmp_path, monkeypatch, bad):
    folder, digest, _ = preview(tmp_path, monkeypatch)
    m = json.loads((folder / "manifest.json").read_text())
    now = NOW
    if bad in ("html", "text"):
        (folder / ("email." + ("html" if bad == "html" else "txt"))).write_text("changed")
    if bad == "image":
        (folder / m["images"][0]["path"]).write_bytes(b"changed")
    if bad == "expired":
        now += timedelta(hours=2)
    if bad == "future":
        now -= timedelta(seconds=1)
    if bad == "sent":
        m["delivery"]["status"] = "full"
    if bad == "run":
        m["run_id"] = "456"
    if bad == "hash":
        digest = "0" * 64
    (folder / "manifest.json").write_text(json.dumps(m))
    with pytest.raises(ValueError):
        send.load_reviewed(folder, digest=digest, run_id="123", attempt="1", now=now)


def test_run_requires_same_main_commit_and_correct_workflow():
    good = dict(
        head_sha="abc",
        head_branch="main",
        path=".github/workflows/formal-test-send.yml",
        event="workflow_dispatch",
        status="completed",
        conclusion="success",
    )
    send.validate_run(good, "abc")
    for key, value in [
        ("head_sha", "old"),
        ("head_branch", "branch"),
        ("path", "other.yml"),
        ("conclusion", "failure"),
    ]:
        with pytest.raises(ValueError):
            send.validate_run({**good, key: value}, "abc")


def test_mock_smtp_sends_exact_reviewed_bytes_and_records_acceptance(tmp_path, monkeypatch):
    import shutil

    folder, digest, html = preview(tmp_path, monkeypatch)
    env = {
        "GITHUB_REPOSITORY": "owner/repo",
        "REVIEWED_RUN_ID": "123",
        "GITHUB_SHA": "abc",
        "GITHUB_RUN_ID": "456",
        "REVIEWED_HTML_SHA256": digest,
        "QQ_EMAIL_ADDRESS": "sender@example.test",
        "QQ_EMAIL_AUTH_CODE": "mock",
        "EMAIL_RECIPIENT": "recipient@example.test",
        "DELIVERY_RECEIPT_PATH": str(tmp_path / "receipt.json"),
        "BRIEF_AUDIT_DIR": str(tmp_path / "sent"),
        "GH_RUN_ID": "456",
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(
        send,
        "gh_json",
        lambda endpoint, **kw: (
            [{"jobs": []}]
            if kw
            else dict(
                head_sha="abc",
                head_branch="main",
                path=".github/workflows/formal-test-send.yml",
                event="workflow_dispatch",
                status="completed",
                conclusion="success",
                run_attempt=1,
            )
        ),
    )

    class Clock:
        now = staticmethod(lambda tz: NOW)
        fromisoformat = staticmethod(datetime.fromisoformat)

    monkeypatch.setattr(send, "datetime", Clock)
    monkeypatch.setattr(
        send.subprocess,
        "run",
        lambda argv, **kw: shutil.copytree(folder, Path(argv[-1]) / folder.name),
    )
    calls = []

    def smtp(**kw):
        calls.append(kw)
        result = DeliveryResult(accepted=("recipient@example.test",), refused={})
        kw["on_progress"](result)
        return result

    monkeypatch.setattr(send, "send_html_email", smtp)
    send.main()
    assert len(calls) == 1 and calls[0]["html_body"] == html
    assert json.loads((tmp_path / "receipt.json").read_text())["accepted_count"] == 1
    manifest = json.loads(next((tmp_path / "sent").glob("*/manifest.json")).read_text())
    assert manifest["sha256"]["html"] == digest and manifest["delivery"]["status"] == "full"
    assert "recipient@example.test" not in json.dumps(manifest)


def test_prior_attempt_acceptance_stops_before_download_or_smtp(monkeypatch):
    for key, value in {
        "GITHUB_REPOSITORY": "owner/repo",
        "REVIEWED_RUN_ID": "123",
        "GITHUB_SHA": "abc",
        "GITHUB_RUN_ID": "456",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(send, "validate_run", lambda *a: None)
    monkeypatch.setattr(
        send,
        "gh_json",
        lambda endpoint, **kw: (
            [{"jobs": [{"steps": [{"name": "Confirm SMTP acceptance", "conclusion": "success"}]}]}]
            if kw
            else {}
        ),
    )
    monkeypatch.setattr(send, "send_html_email", lambda **kw: pytest.fail("SMTP must not run"))
    with pytest.raises(ValueError, match="already has SMTP"):
        send.main()
