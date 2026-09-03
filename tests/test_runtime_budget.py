import pytest

from src.utils.runtime_budget import llm_wall_timeout_seconds


@pytest.mark.parametrize("cutoff,expected", [(None, 900), ("1800", 800), ("3000", 900),
                                           ("999", 0), ("bad", 0), ("NaN", 0), ("inf", 0)])
def test_workflow_cutoff_includes_setup_time_and_never_extends_process_cap(monkeypatch, cutoff, expected):
    monkeypatch.setattr("src.utils.runtime_budget.time.time", lambda: 1000)
    monkeypatch.delenv("BRIEF_LLM_CUTOFF_EPOCH", raising=False)
    if cutoff is not None:
        monkeypatch.setenv("BRIEF_LLM_CUTOFF_EPOCH", cutoff)
    assert llm_wall_timeout_seconds() == expected
