from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from src.processors.llm_client import LLMResponse, LLMUsage
from src.valuation.qqqm import (
    build_qqqm_prompt,
    calculate_qqqm,
    parse_qqqm_inputs,
    prepare_qqqm_display,
)

NOW = datetime(2026, 9, 3, 4, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def no_live_sources(monkeypatch, tmp_path):
    monkeypatch.setattr("src.valuation.qqqm.fetch_source_packet", lambda **kwargs: None)
    monkeypatch.setenv("QQQM_DAILY_FORWARD_ENABLED", "false")
    monkeypatch.setattr("src.valuation.qqqm._BOOTSTRAP_PATH", tmp_path / "missing-bootstrap.json")


def _payload(*, data_date: str = "2026-09-02") -> str:
    fields = {
        "nav_anchor": 500.0,
        "pe_ttm": 25.0,
        "pe_pair_t": 30.0,
        "pe_pair_f": 25.0,
        "div_ttm": 1.5,
        "data_date": data_date,
        "source_urls": [
            "https://www.invesco.com/qqqm",
            "https://www.gurufocus.com/economic_indicators/6778/nasdaq-100-pe-ratio",
            "https://historyofmarket.com/ndx-pe",
        ],
    }
    citations = [
        {
            "field": field,
            "source": fields["source_urls"][2 if field.startswith("pe_pair") else (1 if field == "pe_ttm" else 0)],
            "date": data_date,
            "quote": "verified",
        }
        for index, field in enumerate(
            ("nav_anchor", "pe_ttm", "pe_pair_t", "pe_pair_f", "div_ttm", "data_date")
        )
    ]
    return json.dumps({"status": "ok", "data": fields, "citations": citations})


class _Client:
    def __init__(self, response: LLMResponse) -> None:
        self.response = response
        self.calls = 0

    def search_web(self, *_args, **_kwargs) -> LLMResponse:
        self.calls += 1
        return self.response


def test_parse_and_calculate_only_original_optimistic_scenario() -> None:
    inputs = parse_qqqm_inputs(_payload(), price=300.0, checked_at=NOW)
    result = calculate_qqqm(inputs)
    assert inputs.stale_days == 1
    assert result.value > 0
    assert result.implied_return is not None
    assert result.inputs.price == 300.0
    # Independent cash-flow reference: E_fwd is already year 1, so there is
    # no extra 15% growth in year 1. No conservative/base weights enter here.
    earnings = 24.0  # (500 / 25) * (30 / 25)
    dividends_pv = 0.0
    for year in range(1, 11):
        if year > 1:
            earnings *= 1.15 if year <= 5 else 1.07
        dividends_pv += earnings * 0.075 / 1.1015**year
    expected = dividends_pv + 24.65 * earnings / 1.1015**10
    assert result.value == pytest.approx(expected)
    assert result.warning is None


def test_parse_accepts_search_text_wrapping_valid_json() -> None:
    inputs = parse_qqqm_inputs(
        "检索摘要：来源页面已打开。\n" + _payload() + "\n检索结束。",
        price=300.0,
        checked_at=NOW,
    )
    assert inputs.data_date == "2026-09-02"


def test_parse_rejects_missing_field_citation_and_future_data() -> None:
    payload = json.loads(_payload())
    payload["citations"] = [item for item in payload["citations"] if item["field"] != "nav_anchor"]
    with pytest.raises(ValueError, match="每个输入字段"):
        parse_qqqm_inputs(json.dumps(payload), price=300.0, checked_at=NOW)
    with pytest.raises(ValueError, match="未来"):
        parse_qqqm_inputs(_payload(data_date="2026-09-04"), price=300.0, checked_at=NOW)


def test_missing_forward_pair_never_substitutes_base_value(tmp_path) -> None:
    payload = json.loads(_payload())
    payload["data"]["pe_pair_t"] = None
    payload["data"]["pe_pair_f"] = None
    payload["citations"] = [item for item in payload["citations"] if not item["field"].startswith("pe_pair")]
    inputs = parse_qqqm_inputs(json.dumps(payload), price=300.0, checked_at=NOW)
    with pytest.raises(ValueError, match="乐观情景缺少有效远期 PE 配对"):
        calculate_qqqm(inputs)
    client = _Client(LLMResponse(text=json.dumps(payload), usage=LLMUsage()))
    display = prepare_qqqm_display(price=300, client=client, state_dir=tmp_path, checked_at=NOW)
    assert display.is_pending
    assert not (tmp_path / "qqqm_valuation.json").exists()


def test_prepare_calls_deepseek_once_and_uses_recent_cache_on_failure(tmp_path) -> None:
    good = _Client(LLMResponse(text=_payload(), usage=LLMUsage()))
    first = prepare_qqqm_display(price=300.0, client=good, state_dir=tmp_path, checked_at=NOW)
    assert good.calls == 1
    assert first.intrinsic_value is not None
    assert first.implied_return == pytest.approx(first.intrinsic_value / 300.0 - 1)
    assert first.financial_as_of == "2026-09-02"
    assert first.formula_id == "qqqm_optimistic_cashflow_v1_6"
    assert first.model_version == "1.7"

    failed = _Client(LLMResponse(text=None, error="rate limit", usage=LLMUsage()))
    second = prepare_qqqm_display(price=305.0, client=failed, state_dir=tmp_path, checked_at=NOW)
    assert failed.calls == 1
    assert second.intrinsic_value == pytest.approx(first.intrinsic_value)
    assert second.implied_return != first.implied_return
    assert second.implied_return == pytest.approx(second.intrinsic_value / 305.0 - 1)
    assert "快照" in second.warnings[0]


def test_reject_wrong_index_page():
    payload = _payload().replace("economic_indicators/6778/nasdaq-100-pe-ratio", "stock/FRA:NDX/summary")
    with pytest.raises(ValueError, match="指定 Nasdaq 100"):
        parse_qqqm_inputs(payload, price=300, checked_at=NOW)


def test_corrupt_cache_does_not_crash_email(tmp_path):
    (tmp_path / "qqqm_valuation.json").write_text(json.dumps({"inputs": {"pe_ttm": 0}}))
    failed = _Client(LLMResponse(text=None, error="offline", usage=LLMUsage()))
    display = prepare_qqqm_display(price=300, client=failed, state_dir=tmp_path, checked_at=NOW)
    assert display.is_pending


def test_cached_snapshot_expires_without_renewing_its_data_date(tmp_path):
    client = _Client(LLMResponse(text=_payload(), usage=LLMUsage()))
    prepare_qqqm_display(price=300, client=client, state_dir=tmp_path, checked_at=NOW)
    client.response = LLMResponse(text=None, error="blocked", usage=LLMUsage())
    display = prepare_qqqm_display(price=300, client=client, state_dir=tmp_path, checked_at=NOW + timedelta(days=14))
    assert display.is_pending


def test_reject_not_yet_closed_date():
    with pytest.raises(ValueError, match="已收盘"):
        parse_qqqm_inputs(_payload(data_date="2026-09-03"), price=300, checked_at=NOW)


def test_startup_seed_rechecks_live_values(tmp_path):
    from scripts.seed_qqqm import seed_snapshot

    data = json.loads(_payload())["data"]
    packet = {**data, "fwd_date": data["data_date"]}
    seed_snapshot(_payload(), packet=packet, state_dir=tmp_path, checked_at=NOW)
    assert (tmp_path / "qqqm_valuation.json").exists()
    packet["nav_anchor"] = 600.0
    with pytest.raises(ValueError, match="differs from live"):
        seed_snapshot(_payload(), packet=packet, state_dir=tmp_path, checked_at=NOW)


def test_old_base_only_cache_is_not_relabeled_optimistic(tmp_path):
    payload = json.loads(_payload())
    payload["data"].update(pe_pair_t=None, pe_pair_f=None, fwd_date=None)
    (tmp_path / "qqqm_valuation.json").write_text(json.dumps({
        "source_response": json.dumps(payload), "value": 241.51, "model_version": "1.5",
    }))
    failed = _Client(LLMResponse(text=None, error="offline", usage=LLMUsage()))
    display = prepare_qqqm_display(price=300, client=failed, state_dir=tmp_path, checked_at=NOW)
    assert display.is_pending
    assert display.intrinsic_value is None


def test_complete_legacy_cache_is_recomputed_not_reused_as_weighted_value(tmp_path):
    (tmp_path / "qqqm_valuation.json").write_text(json.dumps({
        "source_response": _payload(), "value": 1.0, "model_version": "1.5",
    }))
    failed = _Client(LLMResponse(text=None, error="offline", usage=LLMUsage()))
    display = prepare_qqqm_display(price=300, client=failed, state_dir=tmp_path, checked_at=NOW)
    expected = calculate_qqqm(parse_qqqm_inputs(_payload(), price=300, checked_at=NOW))
    assert display.intrinsic_value == pytest.approx(expected.value)
    assert display.intrinsic_value != 1.0
    assert display.implied_return == pytest.approx(expected.value / 300 - 1)
    assert display.formula_id == "qqqm_optimistic_cashflow_v1_6"


def test_stale_forward_pair_cannot_produce_an_optimistic_value():
    payload = json.loads(_payload())
    payload["data"]["fwd_date"] = "2026-08-05"
    for citation in payload["citations"]:
        if citation["field"].startswith("pe_pair"):
            citation["date"] = "2026-08-05"
    inputs = parse_qqqm_inputs(json.dumps(payload), price=300, checked_at=NOW)
    with pytest.raises(ValueError, match="乐观情景缺少"):
        calculate_qqqm(inputs)


def test_prompt_and_display_keep_single_scenario_and_gap_return():
    prompt = build_qqqm_prompt(checked_at=NOW, price=300)
    assert "仅采用乐观情景，不做加权平均" in prompt
    assert "不得再乘1.15" in prompt
    assert "20%/40%/40%" not in prompt
    assert "差额收益率，非年化" in prompt


def _packet():
    raw = json.loads(_payload())
    return dict(raw["data"], citations=raw["citations"], fwd_date="2026-09-02")


def test_verified_direct_packet_does_not_depend_on_deepseek(tmp_path, monkeypatch):
    packet = _packet()
    monkeypatch.setattr("src.valuation.qqqm.fetch_source_packet", lambda **kwargs: packet)
    client = _Client(LLMResponse(text=None, error="quota exhausted", usage=LLMUsage()))
    display = prepare_qqqm_display(price=300, client=client, state_dir=tmp_path, checked_at=NOW)
    assert not display.is_pending
    assert client.calls == 0
    assert display.implied_return == pytest.approx(display.intrinsic_value / 300 - 1)
    assert (tmp_path / "qqqm_valuation.json").exists()


def test_search_can_fill_missing_packet_field_but_not_overwrite_verified_pe(tmp_path, monkeypatch):
    packet = _packet()
    packet["pe_pair_t"] = packet["pe_pair_f"] = packet["fwd_date"] = None
    monkeypatch.setattr("src.valuation.qqqm.fetch_source_packet", lambda **kwargs: packet)
    client = _Client(LLMResponse(text=_payload(), usage=LLMUsage()))
    assert not prepare_qqqm_display(price=300, client=client, state_dir=tmp_path, checked_at=NOW).is_pending
    packet["pe_ttm"] = 26
    fresh_state = tmp_path / "mismatch"
    display = prepare_qqqm_display(price=300, client=client, state_dir=fresh_state, checked_at=NOW)
    assert display.is_pending
    assert "pe_ttm 被模型改写" in display.warnings[0]


def test_daily_consensus_requires_explicit_opt_in(tmp_path, monkeypatch):
    packet = _packet()
    packet["forward_basis"] = "dl-blended-fy1fy2"
    for item in packet["citations"]:
        if item["field"].startswith("pe_pair"):
            item["basis"] = packet["forward_basis"]
    monkeypatch.setattr("src.valuation.qqqm.fetch_source_packet", lambda **kwargs: packet)
    client = _Client(LLMResponse(text=None, usage=LLMUsage()))
    assert prepare_qqqm_display(price=300, client=client, state_dir=tmp_path, checked_at=NOW).is_pending
    monkeypatch.setenv("QQQM_DAILY_FORWARD_ENABLED", "true")
    assert not prepare_qqqm_display(price=300, client=client, state_dir=tmp_path, checked_at=NOW).is_pending


def test_weekend_and_not_yet_closed_forward_dates_are_rejected():
    for day in ("2026-09-03", "2026-08-30"):
        payload = json.loads(_payload())
        payload["data"]["fwd_date"] = day
        for citation in payload["citations"]:
            if citation["field"].startswith("pe_pair"):
                citation["date"] = day
        inputs = parse_qqqm_inputs(json.dumps(payload), price=300, checked_at=NOW)
        with pytest.raises(ValueError, match="乐观情景缺少"):
            calculate_qqqm(inputs)


def test_cache_recovery_uses_newest_complete_snapshot_not_existing_invalid_file(tmp_path):
    failed = _Client(LLMResponse(text=None, error="offline", usage=LLMUsage()))
    (tmp_path / "qqqm_valuation.json").write_text('{"source_response":"invalid"}')
    (tmp_path / "qqqm_valuation.daily.json").write_text(json.dumps({"source_response": _payload()}))
    display = prepare_qqqm_display(price=310, client=failed, state_dir=tmp_path, checked_at=NOW)
    assert not display.is_pending
    assert display.implied_return == pytest.approx(display.intrinsic_value / 310 - 1)
    (tmp_path / "qqqm_valuation.json").write_text(json.dumps({"source_response": _payload(data_date="2026-09-01")}))
    display = prepare_qqqm_display(price=310, client=failed, state_dir=tmp_path, checked_at=NOW)
    assert display.financial_as_of == "2026-09-02"


def test_cache_save_gate_does_not_republish_old_base_snapshot(tmp_path):
    from scripts.qqqm_cache import normalize_cache, preserve_daily

    (tmp_path / "qqqm_valuation.json").write_text(json.dumps({"source_response": _payload()}))
    preserve_daily(tmp_path)
    base = json.loads(_payload())
    base["data"].update(pe_pair_t=None, pe_pair_f=None)
    (tmp_path / "qqqm_valuation.json").write_text(json.dumps({"source_response": json.dumps(base)}))
    assert normalize_cache(tmp_path, checked_at=NOW, allow_daily_forward=False)
    restored = json.loads((tmp_path / "qqqm_valuation.json").read_text())
    assert json.loads(restored["source_response"])["data"]["pe_pair_t"] == 30
    assert not normalize_cache(tmp_path, checked_at=NOW + timedelta(days=14), allow_daily_forward=False)


def test_needs_review_reports_which_inputs_are_missing(tmp_path):
    payload = json.loads(_payload())
    payload["status"] = "needs_review"
    payload["data"].update(pe_ttm=None, pe_pair_t=None, pe_pair_f=None)
    client = _Client(LLMResponse(text=json.dumps(payload), usage=LLMUsage()))
    display = prepare_qqqm_display(price=300, client=client, state_dir=tmp_path, checked_at=NOW)
    assert "缺失字段=pe_ttm,pe_pair_t,pe_pair_f" in display.warnings[0]


@pytest.mark.parametrize("bad", [None, [], {}, 12, ""])
def test_malformed_primary_source_response_does_not_block_valid_secondary(tmp_path, bad):
    (tmp_path / "qqqm_valuation.json").write_text(json.dumps({"source_response": bad}))
    (tmp_path / "qqqm_valuation.daily.json").write_text(json.dumps({"source_response": _payload()}))
    failed = _Client(LLMResponse(text=None, error="offline", usage=LLMUsage()))
    assert not prepare_qqqm_display(price=300, client=failed, state_dir=tmp_path, checked_at=NOW).is_pending


def test_bootstrap_snapshot_is_recomputed_and_expires(tmp_path, monkeypatch):
    bootstrap = tmp_path / "verified-startup.json"
    bootstrap.write_text(json.dumps({"source_response": _payload(), "value": 1}))
    monkeypatch.setattr("src.valuation.qqqm._BOOTSTRAP_PATH", bootstrap)
    client = _Client(LLMResponse(text=None, error="offline", usage=LLMUsage()))
    display = prepare_qqqm_display(price=300, client=client, state_dir=tmp_path / "state", checked_at=NOW)
    assert not display.is_pending
    assert display.intrinsic_value != 1
    from scripts.qqqm_cache import normalize_cache
    assert normalize_cache(tmp_path / "state", checked_at=NOW, allow_daily_forward=False)
    display = prepare_qqqm_display(price=300, client=client, state_dir=tmp_path / "state",
                                   checked_at=NOW + timedelta(days=14))
    assert display.is_pending


def test_model_upgrade_does_not_trigger_unattributed_value_jump(tmp_path, monkeypatch):
    from src.valuation.models import ValuationDisplay
    from src.valuation.service import commit_published_values, enforce_jump_guard
    commit_published_values({"QQQM": ValuationDisplay(
        ticker="QQQM", status="current", intrinsic_value=241.51,
        source_document_id="qqqm-v1.5:2026-09-02", model_version="1.5",
    )}, state_dir=tmp_path, sent_at=NOW)
    monkeypatch.setattr("src.valuation.qqqm.fetch_source_packet", lambda **kwargs: _packet())
    client = _Client(LLMResponse(text=None, usage=LLMUsage()))
    display = prepare_qqqm_display(price=300, client=client, state_dir=tmp_path, checked_at=NOW)
    assert not enforce_jump_guard({"QQQM": display}, state_dir=tmp_path)["QQQM"].is_pending


def test_shipped_bootstrap_has_complete_dated_inputs_and_cannot_live_forever():
    snapshot = json.loads((Path(__file__).parents[1] / "config/qqqm_verified_snapshot.json").read_text())
    checked_at = datetime.fromisoformat(snapshot["verified_at"])
    inputs = parse_qqqm_inputs(snapshot["source_response"], price=300, checked_at=checked_at,
                               allow_daily_forward=True)
    assert inputs.forward_basis == "dl-blended-fy1fy2"
    assert calculate_qqqm(inputs).value > 0
    with pytest.raises(ValueError, match="14 天"):
        parse_qqqm_inputs(snapshot["source_response"], price=300,
                           checked_at=checked_at + timedelta(days=15), allow_daily_forward=True)
