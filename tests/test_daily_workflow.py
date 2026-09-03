"""正式晨报 workflow 的手动定向发送安全契约。"""
from __future__ import annotations

from pathlib import Path

_WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "daily.yml"


def test_manual_recipient_override_does_not_change_scheduled_recipients() -> None:
    workflow = _WORKFLOW.read_text(encoding="utf-8")

    assert "recipient_override:" in workflow
    assert "github.event.inputs.recipient_override || secrets.EMAIL_RECIPIENT" in workflow
    assert "EMAIL_RECIPIENT: ${{ secrets.EMAIL_RECIPIENT }}" not in workflow


def test_formal_send_only_restores_read_only_valuation_cache() -> None:
    workflow = _WORKFLOW.with_name("formal-test-send.yml").read_text(encoding="utf-8")
    block = workflow.split("- name: Restore verified Morningstar snapshot only", 1)[1].split("- name:", 1)[0]
    assert "actions/cache/restore@" in block
    assert "path: state/morningstar_fair_values.json" in block
    assert "morningstar-verified-" in block
    assert "daily-state-" not in block
    assert "actions/cache/save@" not in workflow
    assert "EMAIL_RECIPIENT: ${{ inputs.recipients }}" in workflow


def test_only_production_send_saves_isolated_valuation_cache() -> None:
    workflow = _WORKFLOW.read_text(encoding="utf-8")
    block = workflow.split("- name: Save verified Morningstar snapshot", 1)[1]
    assert "github.ref == 'refs/heads/main'" in block
    assert "hashFiles('.delivery-receipt.json') != ''" in block
    assert "hashFiles('state/morningstar_fair_values.json') != ''" in block
    assert "path: state/morningstar_fair_values.json" in block
    assert "key: morningstar-verified-" in block
