"""单元测试:render.py 中的 Jinja2 filter 纯函数。"""

from __future__ import annotations

from datetime import UTC, datetime

from src.renderer.render import (
    _filter_bj_time,
    _filter_metric_delta,
    _filter_metric_num,
    _filter_pct,
    _filter_price,
    _filter_safe_url,
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

    def test_metric_num_nonfinite(self) -> None:
        assert _filter_metric_num(float("nan")) == "—"
        assert _filter_metric_num(float("inf")) == "—"

    def test_metric_delta_signed(self) -> None:
        assert _filter_metric_delta(2.5) == "+2.50"
        assert _filter_metric_delta(-2.5) == "-2.50"

    def test_metric_delta_none(self) -> None:
        assert _filter_metric_delta(None) == "—"

    def test_metric_delta_nonfinite(self) -> None:
        assert _filter_metric_delta(float("nan")) == "—"
        assert _filter_metric_delta(float("-inf")) == "—"


class TestBjTimeFilter:
    def test_utc_to_bj(self) -> None:
        # 2026-04-30 00:00 UTC = 2026-04-30 08:00 北京
        dt = datetime(2026, 4, 30, 0, 0, 0, tzinfo=UTC)
        assert _filter_bj_time(dt) == "04-30 08:00"

    def test_none(self) -> None:
        assert _filter_bj_time(None) == ""


class TestSafeUrlFilter:
    """模板侧 defense-in-depth — 不安全 URL → 空字符串(浏览器忽略)。"""

    def test_https_passes(self) -> None:
        assert _filter_safe_url("https://example.com/path") == "https://example.com/path"

    def test_http_passes(self) -> None:
        assert _filter_safe_url("http://example.com") == "http://example.com"

    def test_javascript_blocked(self) -> None:
        assert _filter_safe_url("javascript:alert(1)") == ""

    def test_data_blocked(self) -> None:
        assert _filter_safe_url("data:text/html,<script>") == ""

    def test_vbscript_blocked(self) -> None:
        assert _filter_safe_url("vbscript:msgbox(1)") == ""

    def test_none_returns_empty(self) -> None:
        assert _filter_safe_url(None) == ""

    def test_empty_returns_empty(self) -> None:
        assert _filter_safe_url("") == ""


class TestSafeUrlInRenderedTemplate:
    """端到端:把 javascript: URL 注入 footnote / 降级 list,渲染产物不能含原 URL。"""

    def test_unsafe_url_in_footnote_not_rendered(self) -> None:
        from datetime import UTC, datetime

        from src.collectors.figures import FigureBundle
        from src.collectors.stocks import StockSignal
        from src.config import HOLDINGS
        from src.processors.figure_filter import FigureFootnote
        from src.renderer.render import render_email

        signal = StockSignal(
            holding=HOLDINGS[0], last_close=None, sma_120=None, sma_200=None,
            delta_120=None, delta_200=None, signal="NONE", error="x",
        )
        bad_footnote = FigureFootnote(index=1, url="javascript:alert(1)", source="X")
        html = render_email(
            signals=[signal],
            generated_at=datetime.now(UTC),
            figures=[FigureBundle(person="X", query="", person_en="X", items=[])],
            figure_summaries=[],
            figure_footnotes=[bad_footnote],
        )
        assert "javascript:alert(1)" not in html, (
            "降级路径模板 href 必须走 safe_url filter,javascript: 不能进 HTML"
        )
