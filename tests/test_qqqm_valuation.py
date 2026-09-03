from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from src.processors.llm_client import LLMResponse, LLMUsage
from src.valuation.qqqm import calculate_qqqm, parse_qqqm_inputs, prepare_qqqm_display

NOW = datetime(2026, 9, 3, 4, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def no_live_sources(monkeypatch):
    monkeypatch.setattr("src.valuation.qqqm.fetch_source_packet", lambda **kwargs: None)


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


def test_parse_and_calculate_qqqm_v15() -> None:
    inputs = parse_qqqm_inputs(_payload(), price=300.0, checked_at=NOW)
    result = calculate_qqqm(inputs)
    assert inputs.stale_days == 1
    assert result.value > 0
    assert result.implied_return is not None
    assert result.inputs.price == 300.0


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


def test_missing_forward_pair_keeps_base_value_available() -> None:
    payload = json.loads(_payload())
    payload["data"]["pe_pair_t"] = None
    payload["data"]["pe_pair_f"] = None
    payload["citations"] = [item for item in payload["citations"] if not item["field"].startswith("pe_pair")]
    inputs = parse_qqqm_inputs(json.dumps(payload), price=300.0, checked_at=NOW)
    result = calculate_qqqm(inputs)
    assert result.value > 0
    assert result.implied_return is not None
    assert result.warning == "B类：乐观缺失，展示基准价值"


def test_prepare_calls_deepseek_once_and_uses_recent_cache_on_failure(tmp_path) -> None:
    good = _Client(LLMResponse(text=_payload(), usage=LLMUsage()))
    first = prepare_qqqm_display(price=300.0, client=good, state_dir=tmp_path, checked_at=NOW)
    assert good.calls == 1
    assert first.intrinsic_value is not None
    assert first.implied_return == pytest.approx(first.intrinsic_value / 300.0 - 1)
    assert first.financial_as_of == "2026-09-02"

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
