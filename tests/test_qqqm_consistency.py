"""Reproduce same-input stability and changing/failed-source counterexamples."""

import copy
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, localcontext
from pathlib import Path
from unittest.mock import Mock

import pytest

from scripts.qqqm_cache import normalize_cache
from src.valuation import qqqm

NOW = datetime(2026, 9, 4, 6, tzinfo=UTC)
BASELINE = Path(__file__).parents[1] / "config/qqqm_verified_snapshot.json"


@pytest.fixture
def packet():
    raw = json.loads(json.loads(BASELINE.read_text())["source_response"])
    # A dated fixture, NOT a claimed current live observation.
    raw["data"]["data_date"] = "2026-09-03"
    for citation in raw["citations"]:
        if citation["field"] in ("nav_anchor", "pe_ttm", "div_ttm"):
            citation["date"] = "2026-09-03"
    return {**raw["data"], "citations": raw["citations"]}


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    monkeypatch.setattr(qqqm, "_BOOTSTRAP_PATH", tmp_path / "no-bootstrap")
    monkeypatch.setenv("QQQM_DAILY_FORWARD_ENABLED", "true")
    monkeypatch.setattr(qqqm, "fetch_source_packet", lambda **kwargs: None)


def text(packet):
    return json.dumps({"status": "ok", "data": packet, "citations": packet["citations"]})


def inputs(packet, price=295.51):
    return qqqm.parse_qqqm_inputs(text(packet), price=price, checked_at=NOW, allow_daily_forward=True)


def change(packet, field, value):
    packet = copy.deepcopy(packet)
    packet[field] = value
    for item in packet["citations"]:
        if item["field"] == field:
            item["quote"] = str(value)
    return packet


def save(path, packet, verified_at=NOW):
    result = qqqm.calculate_qqqm(inputs(packet))
    payload = qqqm.snapshot_payload(result, source_response=text(packet), checked_at=verified_at)
    path.write_text(json.dumps(payload))
    return payload


def run(tmp_path, monkeypatch, packets, price=295.51, checked_at=NOW):
    source = Mock(side_effect=packets)
    monkeypatch.setattr(qqqm, "fetch_source_packet", source)
    client = Mock()
    client.search_web.side_effect = AssertionError("Complete direct inputs must not spend LLM quota")
    display = qqqm.prepare_qqqm_display(price=price, client=client, state_dir=tmp_path, checked_at=checked_at)
    return display, source, client


def test_same_inputs_exact_value_despite_price_dates_url_order_and_decimal_context(packet):
    first = inputs(packet)
    variants = [first, replace(first, price=600), replace(first, data_date="2026-09-04", stale_days=0),
                replace(first, source_urls=tuple(reversed(first.source_urls)))]
    expected = qqqm.calculate_qqqm(first).value
    key = qqqm.calculation_record(first)["input_key"]
    with localcontext() as context:
        context.prec = 6
        context.rounding = ROUND_DOWN
        for item in variants:
            assert qqqm.calculate_qqqm(item).value == expected
            assert qqqm.calculation_record(item)["input_key"] == key
    assert expected == pytest.approx(331.2820647655544, abs=1e-10)


@pytest.mark.parametrize("field", qqqm._VALUE_FIELDS)
def test_real_economic_input_change_changes_key_and_value(packet, field):
    first = inputs(packet)
    second = replace(first, **{field: getattr(first, field) * 1.01})
    assert qqqm.calculation_record(first)["input_key"] != qqqm.calculation_record(second)["input_key"]
    assert qqqm.calculate_qqqm(first).value != qqqm.calculate_qqqm(second).value


def test_model_parameter_and_basis_changes_are_part_of_identity(packet, monkeypatch):
    first = inputs(packet)
    key = qqqm.calculation_record(first)["input_key"]
    assert key != qqqm.calculation_record(replace(first, forward_basis="terminal-consensus"))["input_key"]
    monkeypatch.setattr(qqqm, "_PE_EXIT", 25)
    assert key != qqqm.calculation_record(first)["input_key"]


def test_two_equal_reads_repeat_exact_value_and_only_price_changes_gap(tmp_path, monkeypatch, packet):
    first, source, client = run(tmp_path, monkeypatch, [packet, copy.deepcopy(packet)])
    assert source.call_count == 2
    client.search_web.assert_not_called()
    noisy = copy.deepcopy(packet)
    noisy["source_urls"].reverse()
    noisy["citations"].reverse()
    noisy["pe_transport"] = "reader changed, underlying observation did not"
    second, _, _ = run(tmp_path, monkeypatch, [noisy, packet], price=310)
    assert first.intrinsic_value == second.intrinsic_value
    assert second.implied_return == second.intrinsic_value / 310 - 1
    assert first.implied_return != second.implied_return
    audit = json.loads((tmp_path / qqqm._AUDIT_NAME).read_text())
    assert audit["status"] == "verified"
    assert audit["change"]["same_calculation_inputs"] is True
    assert audit["change"]["value_delta"] == 0
    assert audit["change"]["fields"] == {}


@pytest.mark.parametrize("field", [*qqqm._VALUE_FIELDS, "forward_basis", "data_date", "fwd_date", None])
def test_inconsistent_second_read_keeps_whole_good_snapshot(tmp_path, monkeypatch, packet, field):
    path = tmp_path / "qqqm_valuation.json"
    save(path, packet)
    original = path.read_bytes()
    second = copy.deepcopy(packet)
    if field in qqqm._VALUE_FIELDS:
        second = change(second, field, second[field] * 1.01)
    elif field is not None:
        second[field] = "different"
    else:
        second = None
    display, source, client = run(tmp_path, monkeypatch, [packet, second])
    assert display.status == "not_due"
    assert display.intrinsic_value == qqqm.calculate_qqqm(inputs(packet)).value
    assert path.read_bytes() == original
    assert source.call_count == 2
    client.search_web.assert_not_called()
    audit = json.loads((tmp_path / qqqm._AUDIT_NAME).read_text())
    assert audit["status"] == "cached"
    assert "复核失败" in audit["warning"]
    assert audit["confirmation_source_packet"] == second


def test_stable_source_revision_is_allowed_and_attributed(tmp_path, monkeypatch, packet):
    save(tmp_path / "qqqm_valuation.json", packet, NOW - timedelta(hours=1))
    revised = change(packet, "pe_pair_f", 21.5)
    display, _, _ = run(tmp_path, monkeypatch, [revised, revised])
    assert display.status == "current"
    assert display.intrinsic_value != qqqm.calculate_qqqm(inputs(packet)).value
    audit = json.loads((tmp_path / qqqm._AUDIT_NAME).read_text())
    assert audit["change"]["fields"] == {"pe_pair_f": {"before": 21.21, "after": 21.5}}
    assert audit["change"]["value_delta"] < 0


def test_same_date_cache_merge_and_runtime_use_latest_verified_revision(tmp_path, monkeypatch, packet):
    primary = tmp_path / "qqqm_valuation.json"
    save(primary, packet, NOW - timedelta(hours=1))
    revised = change(packet, "pe_pair_f", 21.5)
    save(tmp_path / "qqqm_valuation.daily.json", revised)
    expected = qqqm.calculate_qqqm(inputs(revised)).value
    cached = qqqm.cached_qqqm_display(price=300, state_dir=tmp_path, checked_at=NOW)
    assert cached.intrinsic_value == expected
    assert normalize_cache(tmp_path, checked_at=NOW, allow_daily_forward=True)
    assert qqqm._from_cache(primary, price=310, checked_at=NOW, allow_daily_forward=True).value == expected


@pytest.mark.parametrize("damage", ["value", "input_key", "recipe", "source", "date", "empty", "schema"])
def test_corrupt_snapshot_cannot_override_independent_good_copy(tmp_path, packet, damage):
    primary = tmp_path / "qqqm_valuation.json"
    payload = save(primary, packet)
    save(tmp_path / "qqqm_valuation.daily.json", packet)
    if damage == "source":
        payload["source_response"] = text(change(packet, "pe_ttm", 29))
    elif damage == "date":
        payload["source_response"] = payload["source_response"].replace("2026-09-03", "2026-09-02")
    elif damage == "empty":
        del payload["calculation"]
    elif damage == "schema":
        payload["schema_version"] = 999
    else:
        payload["calculation"][damage] = "tampered"
    primary.write_text(json.dumps(payload))
    assert qqqm._from_cache(primary, price=300, checked_at=NOW, allow_daily_forward=True) is None
    assert not qqqm.cached_qqqm_display(price=300, state_dir=tmp_path, checked_at=NOW).is_pending


def test_source_can_not_downgrade_pair_date_of_existing_snapshot(tmp_path, monkeypatch, packet):
    save(tmp_path / "qqqm_valuation.json", packet)
    old = copy.deepcopy(packet)
    old["fwd_date"] = "2026-09-01"
    for citation in old["citations"]:
        if citation["field"].startswith("pe_pair"):
            citation["date"] = old["fwd_date"]
    display, _, _ = run(tmp_path, monkeypatch, [old, old])
    assert display.status == "not_due"
    assert "倒退" in display.warnings[0]


def test_dividend_row_order_cannot_change_packet():
    from src.valuation.qqqm_sources import build_source_packet
    from tests.test_qqqm_sources import NOW as SOURCE_NOW
    from tests.test_qqqm_sources import _sources

    nav, dividends = _sources()
    first = build_source_packet(nav, dividends, None, checked_at=SOURCE_NOW)
    dividends["distributions"].reverse()
    assert first == build_source_packet(nav, dividends, None, checked_at=SOURCE_NOW)


def test_both_production_workflows_keep_calculation_evidence():
    root = Path(__file__).parents[1]
    for filename in ("daily.yml", "formal-test-send.yml"):
        workflow = (root / ".github/workflows" / filename).read_text()
        upload = workflow.split("- name: Upload QQQM calculation audit", 1)[1].split("- name:", 1)[0]
        assert "always()" in upload
        assert "path: state/qqqm_calculation_audit.json" in upload
        assert "retention-days: 30" in upload
        if filename == "daily.yml":
            assert workflow.index("rm -f state/qqqm_calculation_audit.json") < workflow.index("- name: Send daily market brief")


def test_same_day_september_3_source_packet_has_independent_tie_out(packet):
    updated = change(change(packet, "nav_anchor", 295.446047), "pe_ttm", 28.61)
    result = qqqm.calculate_qqqm(inputs(updated))
    # Archived observations, not a newly retrieved current market valuation.
    assert result.value == pytest.approx(331.2919213238106, abs=1e-10)
    assert round(result.value, 2) == 331.29


def test_timeout_audit_keeps_candidate_but_marks_actual_fallback(tmp_path, packet):
    save(tmp_path / "qqqm_valuation.json", packet)
    (tmp_path / qqqm._AUDIT_NAME).write_text(json.dumps({
        "status": "collecting", "first_source_packet": packet,
    }))
    display = qqqm.cached_qqqm_display(price=300, state_dir=tmp_path, checked_at=NOW)
    assert display.status == "not_due"
    audit = json.loads((tmp_path / qqqm._AUDIT_NAME).read_text())
    assert audit["status"] == "timeout_cached"
    assert audit["first_source_packet"] == packet
    assert float(audit["selected"]["calculation"]["value"]) == display.intrinsic_value
