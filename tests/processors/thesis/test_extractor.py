"""test_extractor.py — LLM 响应解析、validation、canonicalize、evidence_id 单测"""

import json

from src.processors.thesis.extractor import (
    canonicalize_theme,
    make_evidence_id,
    normalize_text,
    parse_response,
    validate_and_build,
)
from src.processors.thesis.models import ThesisEvidence

# ─── parse_response ────────────────────────────────────────────────


def test_clean_json_array():
    raw = json.dumps([
        {
            "source_section": "company_news",
            "source_name": "Reuters",
            "url": "https://example.com",
            "related_tickers": ["NVDA"],
            "theme": "ai-capex",
            "direction": "support",
            "strength": 4,
            "horizon": "multi_year",
            "text": "测试文本",
            "why_it_matters": "影响 capex 判断",
        }
    ])
    result = parse_response(raw)
    assert len(result) == 1
    assert result[0]["strength"] == 4


def test_markdown_fence_wrapped():
    raw = """```json
[
  {
    "source_section": "company_news",
    "source_name": "Reuters",
    "url": null,
    "related_tickers": ["NVDA"],
    "theme": "ai-capex",
    "direction": "support",
    "strength": 4,
    "horizon": "multi_year",
    "text": "测试",
    "why_it_matters": "测试"
  }
]
```"""
    result = parse_response(raw)
    assert len(result) == 1


def test_markdown_fence_no_lang():
    raw = """```
[
  {"source_section":"voices","source_name":"Dimon","url":null,"related_tickers":["JPM"],"theme":"regulatory","direction":"risk","strength":5,"horizon":"quarterly","text":"x","why_it_matters":"x"}
]
```"""
    result = parse_response(raw)
    assert len(result) == 1


def test_trailing_whitespace():
    raw = '  \n  []  \n'
    result = parse_response(raw)
    assert result == []


def test_invalid_json_returns_empty():
    result = parse_response("这不是 JSON")
    assert result == []


def test_none_input():
    assert parse_response(None) == []


def test_empty_string():
    assert parse_response("") == []


def test_single_dict_wrapped_in_list():
    raw = json.dumps({
        "source_section": "macro", "source_name": "WSJ", "url": None,
        "related_tickers": [], "theme": "trade", "direction": "neutral",
        "strength": 3, "horizon": "quarterly", "text": "x", "why_it_matters": "x",
    })
    result = parse_response(raw)
    assert isinstance(result, list)
    assert len(result) == 1


# ─── canonicalize_theme ────────────────────────────────────────────


def test_canonicalize_spaces_to_hyphens():
    assert canonicalize_theme("AI Inference Cost") == "ai-inference-cost"


def test_canonicalize_underscores():
    assert canonicalize_theme("ai_inference_cost") == "ai-inference-cost"


def test_canonicalize_double_hyphens():
    assert canonicalize_theme("AI--Inference--Cost") == "ai-inference-cost"


def test_canonicalize_company_prefix():
    assert canonicalize_theme("openai-inference-cost") == "inference-cost"


def test_canonicalize_keeps_nonprefix():
    assert canonicalize_theme("inference-cost") == "inference-cost"


# ─── normalize_text ────────────────────────────────────────────────


def test_normalize_text_collapse_whitespace():
    assert normalize_text("  hello   world  ") == "hello world"


def test_normalize_text_lowercase():
    assert normalize_text("Hello WORLD") == "hello world"


# ─── make_evidence_id ──────────────────────────────────────────────


def _make_ev(text="测试文本", **kw):
    return ThesisEvidence(
        evidence_id="",
        date=kw.pop("date", "2026-05-04"),
        source_section=kw.pop("source_section", "company_news"),
        source_name=kw.pop("source_name", "Reuters"),
        url=kw.pop("url", None),
        related_tickers=kw.pop("related_tickers", ["TEST"]),
        theme=kw.pop("theme", "test-theme"),
        direction=kw.pop("direction", "support"),
        strength=kw.pop("strength", 4),
        horizon=kw.pop("horizon", "multi_year"),
        text=text,
        why_it_matters=kw.pop("why_it_matters", "原因"),
    )


def test_evidence_id_stable():
    ev1 = _make_ev(text="测试文本")
    ev2 = _make_ev(text="测试文本")
    assert make_evidence_id(ev1) == make_evidence_id(ev2)


def test_evidence_id_insensitive_to_whitespace():
    ev1 = _make_ev(text="  hello  world ")
    ev2 = _make_ev(text="hello world")
    assert make_evidence_id(ev1) == make_evidence_id(ev2)


def test_evidence_id_different_text():
    ev1 = _make_ev(text="text A")
    ev2 = _make_ev(text="text B")
    assert make_evidence_id(ev1) != make_evidence_id(ev2)


def test_evidence_id_not_empty():
    ev = _make_ev()
    eid = make_evidence_id(ev)
    assert len(eid) == 16


# ─── validate_and_build ────────────────────────────────────────────


def _valid_item(**overrides):
    base = {
        "source_section": "company_news",
        "source_name": "Reuters",
        "url": None,
        "related_tickers": ["TEST"],
        "theme": "test-theme",
        "direction": "support",
        "strength": 4,
        "horizon": "multi_year",
        "text": "测试文本",
        "why_it_matters": "测试原因",
    }
    base.update(overrides)
    return base


def test_valid_item_accepted():
    result = validate_and_build([_valid_item()], "2026-05-04")
    assert len(result) == 1
    assert result[0].date == "2026-05-04"
    assert result[0].strength == 4
    assert result[0].evidence_id  # non-empty


def test_strength_below_3_filtered():
    result = validate_and_build([_valid_item(strength=2)], "2026-05-04")
    assert len(result) == 0


def test_strength_3_accepted():
    result = validate_and_build([_valid_item(strength=3)], "2026-05-04")
    assert len(result) == 1


def test_missing_required_field_filtered():
    result = validate_and_build([{"source_name": "x"}], "2026-05-04")
    assert len(result) == 0


def test_bad_direction_filtered():
    result = validate_and_build([_valid_item(direction="invalid")], "2026-05-04")
    assert len(result) == 0


def test_bad_horizon_filtered():
    result = validate_and_build([_valid_item(horizon="weekly")], "2026-05-04")
    assert len(result) == 0


def test_non_dict_skipped():
    result = validate_and_build(["not a dict", _valid_item()], "2026-05-04")
    assert len(result) == 1


def test_theme_canonicalized():
    result = validate_and_build([_valid_item(theme="AI Capital Expenditure")], "2026-05-04")
    assert result[0].theme == "ai-capital-expenditure"


def test_null_url_preserved():
    result = validate_and_build([_valid_item(url=None)], "2026-05-04")
    assert result[0].url is None
