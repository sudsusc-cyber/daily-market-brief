"""单元测试:render.py 中的 Jinja2 filter 纯函数。"""

from __future__ import annotations

from datetime import UTC, datetime

from src.renderer.render import (
    _filter_bj_time,
    _filter_metric_delta,
    _filter_metric_num,
    _filter_pct,
    _filter_price,
)


class TestPriceFilter:
    def test_normal(self) -> None:
        assert _filter_price(1234.5) == "1,234.50"

    def test_none(self) -> None:
        assert _filter_price(None) == "—"

    def test_zero(self) -> None:
        assert _filter_price(0) == "0.00"


class TestPctFilter:
    def test_positive(self) -> None:
        assert _filter_pct(0.092) == "+9.2%"

    def test_negative(self) -> None:
        assert _filter_pct(-0.05) == "-5.0%"

    def test_none(self) -> None:
        assert _filter_pct(None) == "—"


class TestMetricFilters:
    def test_metric_num_no_unit(self) -> None:
        assert _filter_metric_num(40.53) == "40.53"

    def test_metric_num_unit_arg_ignored(self) -> None:
        # 单位现在由模板单独标注;filter 只输出数字,unit 参数兼容保留但忽略
        assert _filter_metric_num(5.83, unit="%") == "5.83"

    def test_metric_num_large(self) -> None:
        # >= 100 用 1 位小数 + 千分位
        assert _filter_metric_num(1234.567) == "1,234.6"

    def test_metric_num_none(self) -> None:
        assert _filter_metric_num(None) == "—"

    def test_metric_delta_signed(self) -> None:
        assert _filter_metric_delta(2.5) == "+2.50"
        assert _filter_metric_delta(-2.5) == "-2.50"

    def test_metric_delta_none(self) -> None:
        assert _filter_metric_delta(None) == "—"


class TestBjTimeFilter:
    def test_utc_to_bj(self) -> None:
        # 2026-04-30 00:00 UTC = 2026-04-30 08:00 北京
        dt = datetime(2026, 4, 30, 0, 0, 0, tzinfo=UTC)
        assert _filter_bj_time(dt) == "04-30 08:00"

    def test_none(self) -> None:
        assert _filter_bj_time(None) == ""
