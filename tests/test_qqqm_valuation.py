from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from src.processors.llm_client import LLMResponse, LLMUsage
from src.valuation.qqqm import calculate_qqqm, parse_qqqm_inputs, prepare_qqqm_display

NOW = datetime(2026, 9, 3, 4, 0, tzinfo=UTC)


def _payload(*, data_date: str = "2026-09-03") -> str:
    fields = {
        "nav_anchor": 500.0,
        "pe_ttm": 25.0,
        "pe_pair_t": 30.0,
        "pe_pair_f": 25.0,
        "div_ttm": 1.5,
        "data_date": data_date,
        "source_urls": [
            "https://www.invesco.com/qqqm",
            "https://www.gurufocus.com/ndx-pe",
            "https://historyofmarket.com/ndx-pe",
        ],
    }
    citations = [
        {
            "field": field,
            "source": fields["source_urls"][index % 3],
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
    assert inputs.stale_days == 0
    assert result.value > 0
    assert result.implied_return is not None
    assert result.inputs.price == 300.0


def test_parse_rejects_missing_field_citation_and_future_data() -> None:
    payload = json.loads(_payload())
    payload["citations"] = payload["citations"][:-1]
    with pytest.raises(ValueError, match="每个输入字段"):
        parse_qqqm_inputs(json.dumps(payload), price=300.0, checked_at=NOW)
    with pytest.raises(ValueError, match="未来"):
        parse_qqqm_inputs(_payload(data_date="2026-09-04"), price=300.0, checked_at=NOW)


def test_prepare_calls_deepseek_once_and_uses_recent_cache_on_failure(tmp_path) -> None:
    good = _Client(LLMResponse(text=_payload(), usage=LLMUsage()))
    first = prepare_qqqm_display(price=300.0, client=good, state_dir=tmp_path, checked_at=NOW)
    assert good.calls == 1
    assert first.intrinsic_value is not None
    assert first.financial_as_of == "2026-09-03"

    failed = _Client(LLMResponse(text=None, error="rate limit", usage=LLMUsage()))
    second = prepare_qqqm_display(price=305.0, client=failed, state_dir=tmp_path, checked_at=NOW)
    assert failed.calls == 1
    assert second.intrinsic_value == pytest.approx(first.intrinsic_value)
    assert second.implied_return != first.implied_return
    assert "快照" in second.warnings[0]
