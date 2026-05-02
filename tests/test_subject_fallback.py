"""tests/test_subject_fallback.py — 主题兜底测试。"""

from __future__ import annotations

from datetime import date

from src.processors.subject.extractor import (
    MoodInfo,
    SignalSummary,
    SubjectData,
)
from src.processors.subject.fallback import (
    COLD_VARIANTS,
    EXTREME_FEAR_VARIANTS,
    EXTREME_GREED_VARIANTS,
    LUMP_SUM_VARIANTS,
    NEUTRAL_VARIANTS,
    WARM_VARIANTS,
    static_fallback,
)
from src.processors.subject.solar_terms import get_solar_term_context
from src.processors.subject.validator import is_valid


def _data(*, dca=0, lump=0, mood_label="中性", today=date(2026, 5, 1)) -> SubjectData:
    return SubjectData(
        solar_term=get_solar_term_context(today),
        mood=MoodInfo(label=mood_label),
        signals=SignalSummary(
            dca_count=dca, lump_sum_count=lump,
            dca_tickers=["X"] * dca, lump_sum_tickers=["Y"] * lump,
        ),
        holdings_news_top1=None,
        macro_news_top1=None,
        email_full_text="",
    )





class TestFallbackPriority:
    """优先级 + 选词正确(DCA 不参与主题决定,市场情绪驱动)"""

    def test_lump_sum_priority(self) -> None:
        """LUMP-SUM 触发优先级最高(即使有 DCA 与情绪)"""
        s = static_fallback(_data(lump=1, dca=5, mood_label="极度贪婪"))
        suffix = s.split("　")[1]
        assert suffix in LUMP_SUM_VARIANTS
        assert is_valid(s)

    def test_dca_does_not_override_mood(self) -> None:
        """有 DCA 但无 LUMP-SUM 时,看情绪 — DCA 不驱动主题"""
        s = static_fallback(_data(dca=3, mood_label="极度贪婪"))
        suffix = s.split("　")[1]
        # 应该走极度贪婪,而非"DCA 多次加仓"
        assert suffix in EXTREME_GREED_VARIANTS
        assert is_valid(s)

    def test_dca_neutral_falls_to_neutral(self) -> None:
        """有 DCA + 中性情绪 → 走中性闲适意象,不再用 DCA 词库"""
        s = static_fallback(_data(dca=1, mood_label="中性"))
        suffix = s.split("　")[1]
        assert suffix in NEUTRAL_VARIANTS
        assert is_valid(s)

    def test_extreme_greed(self) -> None:
        s = static_fallback(_data(mood_label="极度贪婪"))
        suffix = s.split("　")[1]
        assert suffix in EXTREME_GREED_VARIANTS
        assert is_valid(s)

    def test_extreme_fear(self) -> None:
        s = static_fallback(_data(mood_label="极度恐慌"))
        suffix = s.split("　")[1]
        assert suffix in EXTREME_FEAR_VARIANTS
        assert is_valid(s)

    def test_warm(self) -> None:
        s = static_fallback(_data(mood_label="偏热"))
        suffix = s.split("　")[1]
        assert suffix in WARM_VARIANTS
        assert is_valid(s)

    def test_cold(self) -> None:
        s = static_fallback(_data(mood_label="偏冷"))
        suffix = s.split("　")[1]
        assert suffix in COLD_VARIANTS
        assert is_valid(s)

    def test_full_silence(self) -> None:
        s = static_fallback(_data())
        suffix = s.split("　")[1]
        assert suffix in NEUTRAL_VARIANTS
        assert is_valid(s)


class TestSeasonalPhrase:
    def test_april_uses_guyu(self) -> None:
        s = static_fallback(_data(today=date(2026, 5, 1)))
        assert "谷雨" in s

    def test_january_uses_dahan(self) -> None:
        s = static_fallback(_data(today=date(2026, 1, 25)))
        assert "大寒" in s

    def test_summer_solstice_greed(self) -> None:
        s = static_fallback(_data(today=date(2026, 6, 22), mood_label="极度贪婪"))
        assert "夏至" in s
        suffix = s.split("　")[1]
        assert suffix in EXTREME_GREED_VARIANTS


class TestVariantDiversity:
    """同一情境跨多日产生不同后 4 字 → 缓解重复"""

    def test_dca_across_dates(self) -> None:
        """1 只 DCA 跨 10 个不同日期,应至少出现 3 个不同后 4 字。"""
        results = set()
        for day in range(1, 11):
            d = static_fallback(_data(today=date(2026, 5, day), dca=1))
            results.add(d.split("　")[1])
        assert len(results) >= 3, f"只 {len(results)} 种,多样性不足"

    def test_neutral_across_dates(self) -> None:
        """中性全静默跨 10 日,至少 3 个变体"""
        results = set()
        for day in range(1, 11):
            d = static_fallback(_data(today=date(2026, 4, day)))
            results.add(d.split("　")[1])
        assert len(results) >= 3
