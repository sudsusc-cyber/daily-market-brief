"""正式晨报 workflow 的手动定向发送安全契约。"""
from __future__ import annotations

from pathlib import Path

_WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "daily.yml"


def test_manual_recipient_override_does_not_change_scheduled_recipients() -> None:
    workflow = _WORKFLOW.read_text(encoding="utf-8")

    assert "recipient_override:" in workflow
    assert "github.event.inputs.recipient_override || secrets.EMAIL_RECIPIENT" in workflow
    assert "EMAIL_RECIPIENT: ${{ secrets.EMAIL_RECIPIENT }}" not in workflow
