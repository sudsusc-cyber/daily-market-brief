from __future__ import annotations

from datetime import UTC, datetime

from src.valuation.instructions import FORMULA_INSTRUCTIONS, build_snapshot_draft_prompt
from src.valuation.models import OfficialDocument
from src.valuation.policy import POLICIES


def test_every_instruction_has_guardrails_and_update_cadence() -> None:
    for instruction in FORMULA_INSTRUCTIONS.values():
        assert instruction.projection_rule
        assert instruction.fixed_rules
        assert instruction.forbidden
        assert instruction.update_cadence


def test_prompt_pins_source_hash_formula_and_python_recalculation() -> None:
    now = datetime(2026, 8, 28, tzinfo=UTC)
    document = OfficialDocument(
        document_id="official-doc",
        document_type="10-Q",
        report_period="2026-06-30",
        published_at=now,
        discovered_at=now,
        source_url="https://www.sec.gov/official",
        source_domain="sec.gov",
        content_hash="abc123",
    )
    prompt = build_snapshot_draft_prompt(policy=POLICIES["AAPL"], document=document)
    assert "official-doc" in prompt
    assert "abc123" in prompt
    assert POLICIES["AAPL"].formula_id in prompt
    assert "严禁用新闻、分析师数据、搜索摘要或常识补数" in prompt
    assert "不得输出内在价值、隐含收益率或安全边际" in prompt
