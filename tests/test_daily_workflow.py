"""正式晨报 workflow 的手动定向发送安全契约。"""
from __future__ import annotations

from pathlib import Path

_WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "daily.yml"


def test_manual_recipient_override_does_not_change_scheduled_recipients() -> None:
    workflow = _WORKFLOW.read_text(encoding="utf-8")

    assert "recipient_override:" in workflow
    assert "github.event.inputs.recipient_override || secrets.EMAIL_RECIPIENT" in workflow
    assert "EMAIL_RECIPIENT: ${{ secrets.EMAIL_RECIPIENT }}" not in workflow


def test_formal_send_keeps_morningstar_and_daily_state_read_only() -> None:
    workflow = _WORKFLOW.with_name("formal-test-send.yml").read_text(encoding="utf-8")
    block = workflow.split("- name: Restore verified Morningstar snapshot only", 1)[1].split("- name:", 1)[0]
    assert "actions/cache/restore@" in block
    assert "path: state/morningstar_fair_values.json" in block
    assert "morningstar-verified-" in block
    assert "daily-state-" not in block
    save = workflow.split("- name: Save verified QQQM snapshot", 1)[1]
    assert workflow.count("actions/cache/save@") == 1
    assert "path: state/qqqm_valuation.json" in save
    assert "success() && github.ref == 'refs/heads/main'" in save
    assert "morningstar_fair_values.json" not in save
    assert "daily-state-" not in save
    assert "EMAIL_RECIPIENT: ${{ inputs.recipients }}" in workflow


def test_only_production_send_saves_isolated_valuation_cache() -> None:
    workflow = _WORKFLOW.read_text(encoding="utf-8")
    block = workflow.split("- name: Save verified Morningstar snapshot", 1)[1]
    assert "github.ref == 'refs/heads/main'" in block
    assert "hashFiles('.delivery-receipt.json') != ''" in block
    assert "hashFiles('state/morningstar_fair_values.json') != ''" in block
    assert "path: state/morningstar_fair_values.json" in block
    assert "key: morningstar-verified-" in block


def test_qqqm_restore_preserves_daily_copy_and_never_saves_unvalidated_inputs():
    workflow = _WORKFLOW.read_text(encoding="utf-8")
    preserve = workflow.index("run: uv run python -m scripts.qqqm_cache preserve")
    restore = workflow.index("- name: Restore independent QQQM snapshot")
    assert preserve < restore
    block = workflow[restore:].split("- name:", 2)[1]
    assert "hashFiles" not in block
    for name in ("daily.yml", "formal-test-send.yml"):
        workflow = _WORKFLOW.with_name(name).read_text(encoding="utf-8")
        save = workflow.split("- name: Save verified QQQM snapshot", 1)[1]
        assert "steps.qqqm-cache.outputs.valid == 'true'" in save
        assert "-m scripts.qqqm_cache normalize" in workflow
        assert "QQQM_DAILY_FORWARD_ENABLED" in workflow
