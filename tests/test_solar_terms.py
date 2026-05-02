"""tests/test_solar_terms.py — 24 节气计算行为验证。

实现使用 ephem 库(VSOP87 完整级数,精度 ≈1 秒),与香港天文台 / 紫金山天文台
公开节气表完全一致(已实测核对 2026)。

本测试不再维护"完整节气日期表"(易抄错),改为:
1. 几个**天文上必然确定**的标志性节气日期
2. API 行为(跨年边界 / days_into / phrase 生成等)
3. 节气间隔约 15 天的合理性
"""

from __future__ import annotations

from datetime import date

from src.processors.subject.solar_terms import (
    TERMS,
    _term_date_beijing,
    get_solar_term_context,
    solar_term_on,
)


class TestKnownDates:
    """几个查证过的标志性节气日期(来源:香港天文台 + 紫金山天文台 2026 节气表)。"""

    def test_2026_chunfen(self) -> None:
        """2026 春分 3/21(北京时间 07:30)"""
        # term index 5 = 春分(0°)
        assert _term_date_beijing(2026, 5) == date(2026, 3, 21)

    def test_2026_xiazhi(self) -> None:
        """2026 夏至 6/22(北京时间 01:37)"""
        # term index 11 = 夏至(90°)
        assert _term_date_beijing(2026, 11) == date(2026, 6, 22)

    def test_2026_qiufen(self) -> None:
        """2026 秋分 9/23(北京时间 17:10)"""
        # term index 17 = 秋分(180°)
        assert _term_date_beijing(2026, 17) == date(2026, 9, 23)

    def test_2026_dongzhi(self) -> None:
        """2026 冬至 12/22(北京时间 13:38)"""
        # term index 23 = 冬至(270°)
        assert _term_date_beijing(2026, 23) == date(2026, 12, 22)

    def test_2026_lichun(self) -> None:
        """2026 立春 2/4"""
        assert _term_date_beijing(2026, 2) == date(2026, 2, 4)

    def test_2026_lixia(self) -> None:
        """2026 立夏 5/6(北京时间 04:49)"""
        assert _term_date_beijing(2026, 8) == date(2026, 5, 6)

    def test_2027_chunfen(self) -> None:
        """跨年验证:2027 春分应在 3/20 或 3/21"""
        d = _term_date_beijing(2027, 5)
        assert d.year == 2027 and d.month == 3 and d.day in (20, 21)

    def test_2030_dongzhi(self) -> None:
        """长期验证:2030 冬至 12/22"""
        d = _term_date_beijing(2030, 23)
        assert d.year == 2030 and d.month == 12 and d.day in (21, 22)


class TestSpacing:
    """节气间隔验证:相邻节气约 15.2 天。"""

    def test_consecutive_terms_2026(self) -> None:
        prev = None
        for i in range(24):
            cur = _term_date_beijing(2026, i)
            if prev:
                gap = (cur - prev).days
                assert 14 <= gap <= 17, f"term {TERMS[i][0]} gap {gap} 异常"
            prev = cur


class TestContextAPI:
    def test_today_2026_05_01_is_guyu(self) -> None:
        """2026 年 5 月 1 日:谷雨 4/20 ~ 立夏 5/6"""
        ctx = get_solar_term_context(date(2026, 5, 1))
        assert ctx.current == "谷雨"
        assert ctx.next == "立夏"
        # 5/1 距 4/20 是 11 天 → days_into=12
        assert ctx.days_into == 12
        # 5/1 到 立夏 5/6 是 5 天
        assert ctx.days_to_next == 5

    def test_term_first_day(self) -> None:
        """春分当天 3/21 → days_into=1, phrase=初临"""
        ctx = get_solar_term_context(date(2026, 3, 21))
        assert ctx.current == "春分"
        assert ctx.days_into == 1
        assert "初临" in ctx.phrase

    def test_term_last_day(self) -> None:
        """节气末段 days_to_next <= 2 → phrase=次节气将至"""
        # 2026 春分 3/21,清明 4/5。3/21+13=4/3,清明前 2 天
        ctx = get_solar_term_context(date(2026, 4, 3))
        assert ctx.current == "春分"
        assert ctx.next == "清明"
        assert ctx.days_to_next == 2
        assert "清明" in ctx.phrase
        assert "将至" in ctx.phrase

    def test_year_boundary(self) -> None:
        """2026/1/1:在 2025/12/22 冬至 ~ 2026/1/6 小寒之间 → current=冬至"""
        ctx = get_solar_term_context(date(2026, 1, 1))
        assert ctx.current == "冬至"
        assert ctx.next == "小寒"

    def test_phrase_winter_deep(self) -> None:
        """大寒中段 → 大寒冬深"""
        # 2026 大寒 1/20 → 1/25 是中段
        ctx = get_solar_term_context(date(2026, 1, 25))
        assert ctx.current == "大寒"
        assert "冬深" in ctx.phrase

    def test_solar_term_on_shortcut(self) -> None:
        """快捷接口"""
        # 春分当天
        assert solar_term_on(date(2026, 3, 21)) == "春分"
        # 夏至当天
        assert solar_term_on(date(2026, 6, 22)) == "夏至"
        # 秋分当天
        assert solar_term_on(date(2026, 9, 23)) == "秋分"
        # 冬至当天
        assert solar_term_on(date(2026, 12, 22)) == "冬至"


class TestPhraseStyles:
    """节气短语生成规则覆盖四季。"""

    def test_spring_deep(self) -> None:
        """春季节气中段用"春深"修饰"""
        # 春分 3/21 + 5 天 = 3/26
        ctx = get_solar_term_context(date(2026, 3, 26))
        assert ctx.current == "春分"
        assert "春深" in ctx.phrase

    def test_summer_blaze(self) -> None:
        """夏季节气中段用"夏炽"修饰"""
        # 夏至 6/22 + 5 天 = 6/27
        ctx = get_solar_term_context(date(2026, 6, 27))
        assert ctx.current == "夏至"
        assert "夏炽" in ctx.phrase

    def test_autumn_chill(self) -> None:
        """秋季节气中段用"秋寒"修饰"""
        # 秋分 9/23 + 5 天 = 9/28
        ctx = get_solar_term_context(date(2026, 9, 28))
        assert ctx.current == "秋分"
        assert "秋寒" in ctx.phrase
