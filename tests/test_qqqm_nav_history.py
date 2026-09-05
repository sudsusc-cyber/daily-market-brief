"""Official dated NAV gate, outage recovery and pre-gate snapshot migration."""

import copy
import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from unittest.mock import Mock

import pytest

from src.valuation import qqqm
from src.valuation import qqqm_sources as sources
from tests.test_qqqm_sources import _history, _sources

NOW = datetime(2026, 9, 4, 6, tzinfo=UTC)
ANCHOR = date(2026, 9, 3)


@pytest.fixture
def source_mocks(monkeypatch):
    nav, dividends = _sources()
    nav.update(effectiveDate="2026-09-03", nav=295.446047)
    pair = {"trailing": [{"date": "2026-09-03", "value": 27.79}], "forwardOwn": [
        {"date": "2026-09-03", "value": 21.35, "basis": sources.DAILY_FORWARD_BASIS},
    ]}
    responses = {sources.NAV_URL: nav, sources.NAV_HISTORY_URL: _history(),
                 sources.DIV_URL: dividends, sources.PAIR_URL: pair}
    monkeypatch.setattr(sources, "_fetch", lambda url: copy.deepcopy(responses.get(url)))
    monkeypatch.setattr(sources, "fetch_dividend_backup", lambda **kwargs: copy.deepcopy(dividends))
    monkeypatch.setattr(sources, "fetch_gurufocus_pe", lambda **kwargs: {
        "value": 28.61, "date": "2026-09-03", "transport": sources.PE_URL})
    monkeypatch.setenv("QQQM_DAILY_FORWARD_ENABLED", "true")
    return responses


def _run(state_dir):
    client = Mock()
    client.search_web.side_effect = AssertionError("NAV failure must not consume LLM quota")
    result = qqqm.prepare_qqqm_display(price=295.2041015625, client=client, state_dir=state_dir, checked_at=NOW)
    client.search_web.assert_not_called()
    return result


def test_exact_day_not_first_row_or_other_price_series():
    history = _history()
    history["lineChartData"].insert(0, {"type": "Market Price", "data": [
        {"date": "09/02/2026", "value": 999},
    ]})
    evidence = sources.verify_nav_history(history, anchor=date(2026, 9, 2), nav_value=292.029084)
    assert evidence["date"] == "2026-09-02"
    assert evidence["value"] == 292.029084
    assert evidence["source_url"] == sources.NAV_HISTORY_URL
    assert len(evidence["payload_sha256"]) == 64


@pytest.mark.parametrize("damage", ["missing", "duplicate", "conflict", "wrong_cusip", "currency", "series",
                                    "duplicate_series", "series_missing", "bad_row", "bad_date", "no_history"])
def test_reject_incomplete_ambiguous_or_wrong_security_history(damage):
    history = _history()
    series = history["lineChartData"][0]
    if damage == "missing":
        series["data"].pop(0)
    elif damage in ("duplicate", "conflict"):
        series["data"].append({"date": "09/03/2026", "value": 295.446047 if damage == "duplicate" else 292})
    elif damage == "wrong_cusip":
        history["cusip"] = "QQQ"
    elif damage == "currency":
        history["currency"] = "HKD"
    elif damage == "series":
        series["type"] = "Market Price"
    elif damage == "duplicate_series":
        history["lineChartData"].append(copy.deepcopy(series))
    elif damage == "series_missing":
        history["lineChartData"] = None
    elif damage == "bad_row":
        series["data"].append(None)
    elif damage == "bad_date":
        series["data"][0]["date"] = "2026-09-03"
    else:
        history = None
    with pytest.raises(ValueError):
        sources.verify_nav_history(history, anchor=ANCHOR, nav_value=295.446047)


@pytest.mark.parametrize("value", [True, False, None, "NaN", float("inf"), -1, 0])
def test_reject_invalid_history_value(value):
    history = _history()
    history["lineChartData"][0]["data"][0]["value"] = value
    with pytest.raises(ValueError):
        sources.verify_nav_history(history, anchor=ANCHOR, nav_value=295.446047)


def test_reject_even_last_decimal_mismatch():
    with pytest.raises(ValueError, match="mismatch"):
        sources.verify_nav_history(_history(), anchor=ANCHOR, nav_value=295.446048)


def test_weekend_carry_forward_cannot_replace_required_trading_day():
    # Monday Labor Day: the anchor must be Friday, not Sunday's repeated value.
    anchor = sources.latest_closed_date(datetime(2026, 9, 7, 23, tzinfo=UTC))
    assert anchor == date(2026, 9, 4)
    history = _history()
    history["lineChartData"][0]["data"].insert(0, {"date": "09/06/2026", "value": 295.446047})
    with pytest.raises(ValueError, match="missing"):
        sources.verify_nav_history(history, anchor=anchor, nav_value=295.446047)
    with pytest.raises(ValueError, match="trading day"):
        sources.verify_nav_history(history, anchor=date(2026, 9, 6), nav_value=295.446047)
    history["lineChartData"][0]["data"].append({"date": "09/04/2026", "value": 295.446047})
    assert sources.verify_nav_history(history, anchor=anchor, nav_value=295.446047)["date"] == "2026-09-04"


def test_identical_nav_on_consecutive_trading_days_is_allowed():
    history = _history()
    history["lineChartData"][0]["data"][0]["value"] = 292.029084
    assert sources.verify_nav_history(history, anchor=ANCHOR, nav_value=292.029084)["value"] == 292.029084


def test_official_plain_text_json_is_accepted(monkeypatch):
    import requests

    response = requests.Response()
    response.status_code = 200
    response.headers["Content-Type"] = "text/plain;charset=UTF-8"
    response._content = json.dumps(_history()).encode()
    monkeypatch.setattr(sources.requests, "get", lambda *args, **kwargs: response)
    assert sources._fetch(sources.NAV_HISTORY_URL) == _history()


def test_real_september_3_inputs_tie_to_325_95_and_save_both_nav_reads(tmp_path, source_mocks):
    result = _run(tmp_path)
    assert result.status == "current"
    assert result.intrinsic_value == pytest.approx(325.9526489556774, abs=1e-10)
    assert result.implied_return == result.intrinsic_value / 295.2041015625 - 1
    snapshot = json.loads((tmp_path / qqqm._CACHE_NAME).read_text())
    assert snapshot["schema_version"] == 3
    assert snapshot["nav_history_evidence"]["date"] == "2026-09-03"
    assert snapshot["nav_history_evidence"]["value"] == 295.446047
    audit = json.loads((tmp_path / qqqm._AUDIT_NAME).read_text())
    for key in ("first_source_packet", "confirmation_source_packet"):
        assert audit[key]["nav_history_evidence"] == snapshot["nav_history_evidence"]
    assert audit["selected"]["calculation"]["input_key"] == "d8b145a0dbd32c9f6425795384846a73d503d935c85b218cff56a7beb49ea15c"


@pytest.mark.parametrize("failure", ["misdated", "history_down", "no_target_day"])
def test_stable_wrong_current_date_or_history_outage_keeps_whole_verified_cache(tmp_path, source_mocks, failure):
    before = _run(tmp_path)
    path = tmp_path / qqqm._CACHE_NAME
    original = path.read_bytes()
    if failure == "misdated":
        # The observed bug: both reads could say Sep 3 while carrying Sep 2 NAV.
        source_mocks[sources.NAV_URL]["nav"] = 292.029084
    elif failure == "history_down":
        source_mocks[sources.NAV_HISTORY_URL] = None
    else:
        source_mocks[sources.NAV_HISTORY_URL]["lineChartData"][0]["data"].pop(0)
    # Stable bad input remains invalid however often the endpoint repeats it.
    assert sources.fetch_source_packet(checked_at=NOW, allow_daily_forward=True) is None
    assert sources.fetch_source_packet(checked_at=NOW, allow_daily_forward=True) is None
    after = _run(tmp_path)
    assert after.status == "not_due"
    assert after.intrinsic_value == before.intrinsic_value
    assert after.financial_as_of == before.financial_as_of
    assert path.read_bytes() == original


def test_unverified_direct_packet_cannot_be_rescued_by_a_search_claim(tmp_path, monkeypatch, source_mocks):
    valid = _run(tmp_path)
    packet = sources.fetch_source_packet(checked_at=NOW, allow_daily_forward=True)
    del packet["nav_history_evidence"]
    monkeypatch.setattr(qqqm, "fetch_source_packet", lambda **kwargs: packet)
    assert _run(tmp_path).intrinsic_value == valid.intrinsic_value


@pytest.mark.parametrize("damage", ["missing", "value", "date", "url", "currency", "fingerprint"])
def test_corrupt_nav_evidence_never_survives_offline_cache_gate(tmp_path, source_mocks, damage):
    _run(tmp_path)
    path = tmp_path / qqqm._CACHE_NAME
    payload = json.loads(path.read_text())
    if damage == "missing":
        del payload["nav_history_evidence"]
    elif damage == "fingerprint":
        payload["nav_history_evidence_sha256"] = "0" * 64
    else:
        field, value = {"value": ("value", 292.029084), "date": ("date", "2026-09-02"),
                        "url": ("source_url", sources.NAV_URL), "currency": ("currency", "HKD")}[damage]
        payload["nav_history_evidence"][field] = value
    path.write_text(json.dumps(payload))
    assert qqqm._from_cache(path, price=300, checked_at=NOW, allow_daily_forward=True) is None


def test_legacy_migration_keeps_valid_325_95_but_rejects_misdated_327_66(tmp_path, source_mocks):
    _run(tmp_path)
    path = tmp_path / qqqm._CACHE_NAME
    payload = json.loads(path.read_text())
    payload.update(schema_version=2, model_version="1.8")
    del payload["nav_history_evidence"]
    del payload["nav_history_evidence_sha256"]
    raw = json.loads(payload["source_response"])
    del raw["data"]["nav_history_evidence"]

    def write_legacy():
        source = json.dumps(raw)
        inputs = qqqm.parse_qqqm_inputs(source, price=300, checked_at=NOW, allow_daily_forward=True)
        payload.update(source_response=source, source_response_sha256=hashlib.sha256(source.encode()).hexdigest(),
                       calculation=qqqm.calculation_record(inputs))
        path.write_text(json.dumps(payload))
        return qqqm.calculate_qqqm(inputs).value

    write_legacy()
    assert qqqm._from_cache(path, price=300, checked_at=NOW, allow_daily_forward=True).value == pytest.approx(325.9526489556774)
    raw["data"].update(nav_anchor=292.029084, pe_pair_t=28.06, pe_pair_f=21.21, fwd_date="2026-09-02")
    for item in raw["citations"]:
        item["quote"] = str(raw["data"][item["field"]])
        if item["field"].startswith("pe_pair"):
            item["date"] = "2026-09-02"
    assert write_legacy() == pytest.approx(327.6582003545837)
    assert qqqm._from_cache(path, price=300, checked_at=NOW, allow_daily_forward=True) is None


def test_migration_does_not_extend_bootstrap_observation_age():
    valid = qqqm._from_cache(qqqm._BOOTSTRAP_PATH, price=300, checked_at=NOW, allow_daily_forward=True)
    assert valid.value == pytest.approx(331.2820647655544)
    assert valid.inputs.data_date == "2026-09-02"
    assert qqqm._from_cache(qqqm._BOOTSTRAP_PATH, price=300, checked_at=NOW + timedelta(days=14),
                           allow_daily_forward=True) is None


@pytest.mark.parametrize("observations", [None, [], "bad", 1])
def test_corrupt_migration_registry_fails_closed_without_crashing(tmp_path, monkeypatch, observations):
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps({"observations": observations}))
    monkeypatch.setattr(qqqm, "_LEGACY_NAV_PATH", path)
    assert qqqm._from_cache(qqqm._BOOTSTRAP_PATH, price=300, checked_at=NOW, allow_daily_forward=True) is None
