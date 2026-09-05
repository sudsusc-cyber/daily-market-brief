from datetime import date, timedelta
from unittest.mock import Mock

import pytest

from src.processors.subject import generator
from src.processors.subject.extractor import MoodInfo, SignalSummary, SubjectData
from src.processors.subject.fallback import static_fallback
from src.processors.subject.prompts import build_user_prompt
from src.processors.subject.solar_terms import get_solar_term_context, season_of
from src.processors.subject.validator import validate


def data_on(day, mood="偏热"):
    return SubjectData(
        solar_term=get_solar_term_context(day), mood=MoodInfo(label=mood),
        signals=SignalSummary(), holdings_news_top1=None,
        macro_news_top1=None, email_full_text="",
    )


@pytest.mark.parametrize("subject", [
    "处暑秋意　蝉鸣未歇", "处暑秋寒　闲看云起", "处暑秋意　霜风骤起",
    "谷雨春深　卧听涛声", "秋分初临　闲看云起",
])
def test_reject_wrong_season_phase_or_term(subject):
    ctx = get_solar_term_context(date(2026, 9, 5))
    assert not validate(subject, season="autumn", solar_term=ctx)[0]


def test_next_term_only_within_two_days():
    for day, allowed in [(date(2026, 9, 1), False), (date(2026, 9, 5), True)]:
        ctx = get_solar_term_context(day)
        assert validate("白露将至　静水流深", "autumn", solar_term=ctx)[0] is allowed


def test_cache_and_both_llm_passes_cannot_bypass(monkeypatch, tmp_path):
    today = date(2026, 9, 5)
    monkeypatch.setattr(generator, "CACHE_PATH", tmp_path / "cache.json")
    monkeypatch.setattr(generator, "LOG_PATH", tmp_path / "log.jsonl")
    monkeypatch.setattr(generator.time, "sleep", lambda _: None)
    generator._save_cache({today.isoformat(): "处暑秋意　蝉鸣未歇"})
    call = Mock(side_effect=[("处暑秋寒　闲看云起", None), ("处暑秋意　蝉鸣未歇", None)])
    monkeypatch.setattr(generator, "_call_deepseek", call)
    data = data_on(today)
    actual = generator.generate_subject(data, llm=None, today_bj=today)
    assert call.call_count == 2
    assert actual == static_fallback(data)
    assert validate(actual, "autumn", solar_term=data.solar_term)[0]


def test_full_year_fallback_all_moods():
    for offset in range(365):
        for mood in ("极度恐慌", "偏冷", "中性", "偏热", "极度贪婪"):
            data = data_on(date(2026, 1, 1) + timedelta(days=offset), mood)
            actual = static_fallback(data)
            assert validate(actual, season_of(data.solar_term.current), solar_term=data.solar_term)[0]


def test_prompt_has_actual_date_and_season_taboo():
    prompt = build_user_prompt(data_on(date(2026, 9, 5)))
    assert "北京日期:2026-09-05" in prompt
    assert "下一节气:白露" in prompt
    assert "当前季节禁用意象:蝉" in prompt
