"""正式晨报 workflow 的手动定向发送安全契约。"""
from __future__ import annotations

from pathlib import Path

_WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "daily.yml"


def test_manual_recipient_override_cannot_consume_production_delivery() -> None:
    workflow = _WORKFLOW.read_text(encoding="utf-8")

    assert "recipient_override" not in workflow
    assert "EMAIL_RECIPIENT: ${{ secrets.EMAIL_RECIPIENT }}" in workflow


def test_formal_send_only_persists_isolated_valuation_state() -> None:
    workflow = _WORKFLOW.with_name("formal-test-send.yml").read_text(encoding="utf-8")
    block = workflow.split("- name: Restore verified Morningstar snapshot only", 1)[1].split("- name:", 1)[0]
    assert "actions/cache/restore@" in block
    assert "path: state/morningstar_fair_values.json" in block
    assert "morningstar-verified-" in block
    assert "daily-state-" not in block
    save = workflow.split("- name: Save verified QQQM snapshot", 1)[1]
    assert workflow.count("actions/cache/save@") == 3
    morningstar_save = workflow.split("- name: Save verified Morningstar history", 1)[1].split("- name:", 1)[0]
    assert "github.ref == 'refs/heads/main'" in morningstar_save
    assert "hashFiles('.delivery-receipt.json') != ''" in morningstar_save
    assert "path: state/morningstar_fair_values.json" in morningstar_save
    assert "daily-state-" not in workflow
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


def test_daily_and_formal_jobs_share_extended_timeout_and_early_send_reserve():
    for name in ("daily.yml", "formal-test-send.yml"):
        workflow = _WORKFLOW.with_name(name).read_text(encoding="utf-8")
        assert "timeout-minutes: 20" in workflow
        assert "BRIEF_LLM_CUTOFF_EPOCH=$(( $(date +%s) + 17 * 60 ))" in workflow
        assert workflow.index("Set LLM cutoff before setup") < workflow.index("actions/checkout@")


def test_pop_mart_recovery_never_overwrites_daily_history_and_saves_only_on_main():
    for name in ("daily.yml", "formal-test-send.yml"):
        workflow = _WORKFLOW.with_name(name).read_text(encoding="utf-8")
        restore = workflow.split("- name: Restore isolated Pop Mart analyst target", 1)[1].split("- name:", 1)[0]
        assert "path: state/pop_mart_analyst_recovery.json" in restore
        assert "pop_mart_analyst_target.json" not in restore
        assert "run: uv run python -m scripts.pop_mart_cache" in workflow
        save = workflow.split("- name: Save isolated Pop Mart analyst target", 1)[1].split("- name:", 1)[0]
        assert "github.ref == 'refs/heads/main'" in save
        assert "hashFiles('.delivery-receipt.json') != ''" in save
        assert "path: state/pop_mart_analyst_recovery.json" in save
