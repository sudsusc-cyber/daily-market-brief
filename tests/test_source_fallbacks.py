"""外部数据源主备链路回归测试（全部 mock，不访问公网）。"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from src.collectors import (
    company_news,
    figure_official_sources,
    figures,
    frontier_labs,
    jiangsu_fuel,
    sentiment,
    stocks,
)
from src.collectors.company_news import CompanyNewsBundle, NewsItem
from src.collectors.frontier_labs import FrontierLab
from src.config import HOLDINGS

_NOW = datetime(2026, 8, 17, 4, 0, tzinfo=UTC)


def _news_item(title: str = "fallback") -> NewsItem:
    return NewsItem(
        title=title,
        published_at=_NOW - timedelta(hours=1),
        url="https://example.com/fallback",
        source="Fallback",
    )


def test_us_company_news_falls_back_from_finnhub_to_google(monkeypatch) -> None:
    holding = HOLDINGS[0]
    monkeypatch.setattr(
        company_news,
        "_collect_via_finnhub",
        lambda *_: CompanyNewsBundle(
            holding=holding,
            error="Finnhub 503",
            data_source="finnhub",
        ),
    )
    monkeypatch.setattr(
        company_news,
        "_collect_via_google_news",
        lambda *_args, **_kwargs: CompanyNewsBundle(
            holding=holding,
            items=[_news_item()],
            data_source="google_news_en",
        ),
    )

    bundle = company_news.fetch_one(holding, object())

    assert bundle.error is None
    assert bundle.data_source == "google_news_en"
    assert len(bundle.items) == 1


def test_hk_company_news_falls_back_from_google_to_finnhub(monkeypatch) -> None:
    holding = next(item for item in HOLDINGS if item.ticker == "0700.HK")
    monkeypatch.setattr(
        company_news,
        "_collect_via_google_news",
        lambda *_args, **_kwargs: CompanyNewsBundle(
            holding=holding,
            error="Google News 503",
            data_source="google_news_cn",
        ),
    )
    monkeypatch.setattr(
        company_news,
        "_collect_via_finnhub",
        lambda *_: CompanyNewsBundle(
            holding=holding,
            items=[_news_item()],
            data_source="finnhub",
        ),
    )

    bundle = company_news.fetch_one(holding, object())

    assert bundle.error is None
    assert bundle.data_source == "finnhub"


def test_figure_google_failure_uses_finnhub_company_news(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        figures,
        "FIGURES",
        [("黄仁勋", '"Jensen Huang"', "en", "Jensen Huang")],
    )
    monkeypatch.setattr(
        figures,
        "last_24h_window",
        lambda: (_NOW - timedelta(hours=24), _NOW),
    )
    monkeypatch.setattr(
        figures,
        "_fetch_google_news",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("Google 503")),
    )
    monkeypatch.setattr(
        figures,
        "_fetch_finnhub_company_news",
        lambda *_args, **_kwargs: [{
            "headline": "Jensen Huang said demand remains strong",
            "summary": "Jensen Huang said the company expects more demand.",
            "datetime": int((_NOW - timedelta(hours=1)).timestamp()),
            "url": "https://example.com/jensen",
            "source": "Reuters",
        }],
    )
    monkeypatch.setattr(figure_official_sources, "fetch_all", lambda *_: {})

    bundles, _ = figures.fetch_all(
        tmp_path / "figures.json",
        finnhub_api_key="test-key",
    )

    assert bundles[0].error is None
    assert len(bundles[0].items) == 1
    assert bundles[0].items[0].source == "Reuters"


def test_frontier_google_failure_uses_finnhub_general_news(tmp_path, monkeypatch) -> None:
    lab = FrontierLab(
        name="Anthropic",
        queries=['"Anthropic"'],
        official_feeds=[],
        related_tickers=["GOOG", "NVDA"],
    )
    monkeypatch.setattr(frontier_labs, "FRONTIER_LABS", [lab])
    monkeypatch.setattr(
        frontier_labs,
        "last_24h_window",
        lambda: (_NOW - timedelta(hours=24), _NOW),
    )
    monkeypatch.setattr(
        frontier_labs,
        "_fetch_google_news",
        lambda *_: (_ for _ in ()).throw(RuntimeError("Google 503")),
    )
    monkeypatch.setattr(
        frontier_labs,
        "_fetch_finnhub_general_news",
        lambda *_: [{
            "headline": "Anthropic signs a new cloud agreement",
            "summary": "Anthropic expands enterprise deployment.",
            "datetime": int((_NOW - timedelta(hours=1)).timestamp()),
            "url": "https://example.com/anthropic",
            "source": "Reuters",
        }],
    )

    bundles, _ = frontier_labs.fetch_all(
        tmp_path / "frontier.json",
        finnhub_api_key="test-key",
    )

    assert bundles[0].errors == []
    assert len(bundles[0].items) == 1
    assert bundles[0].items[0].source_type == "finnhub"


def test_stock_yfinance_failure_uses_direct_chart(monkeypatch) -> None:
    monkeypatch.setattr(
        stocks,
        "_yf_history",
        lambda *_: (_ for _ in ()).throw(RuntimeError("crumb failed")),
    )
    monkeypatch.setattr(
        stocks,
        "_yahoo_chart_weekly",
        lambda _symbol: ([100.0 + index for index in range(220)], 321.0),
    )

    signal = stocks.fetch_one(HOLDINGS[0])

    assert signal.error is None
    assert signal.data_source == "yahoo_chart"
    assert signal.last_close == 321.0


def test_sentiment_yfinance_failure_uses_direct_chart(monkeypatch) -> None:
    monkeypatch.setattr(
        sentiment,
        "_fetch_yfinance_close",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("crumb failed")),
    )
    monkeypatch.setattr(
        sentiment,
        "_fetch_yahoo_chart_close",
        lambda *_args, **_kwargs: [98.0, 99.0],
    )

    metric = sentiment._fetch_simple_index("DX-Y.NYB", "DXY")

    assert metric.error is None
    assert metric.current == 99.0
    assert metric.prior == 98.0


def test_oil_crude_proxy_falls_back_to_fred(monkeypatch) -> None:
    monkeypatch.setattr(
        jiangsu_fuel,
        "_fetch_crude_history",
        lambda *_: (_ for _ in ()).throw(RuntimeError("Yahoo 503")),
    )
    monkeypatch.setattr(
        jiangsu_fuel,
        "_fetch_fred_crude_closes",
        lambda *_args, **_kwargs: [100.0] * 10 + [90.0] * 10,
    )

    estimate = jiangsu_fuel._estimate_direction_from_crude(
        date(2026, 8, 17),
        fred_api_key="test-key",
    )

    assert estimate is not None
    assert estimate[0] == "下调"
    assert estimate[1] == -0.1


def test_berkshire_parser_requests_supported_compression(monkeypatch) -> None:
    captured_headers: dict[str, str] = {}

    class _Response:
        content = b'<html><a href="news0814.html">August 14, 2026</a></html>'

        def raise_for_status(self) -> None:
            return None

    def fake_get(_url, *, headers, timeout):  # noqa: ANN001
        captured_headers.update(headers)
        assert timeout == figure_official_sources._REQUEST_TIMEOUT
        return _Response()

    monkeypatch.setattr(figure_official_sources.requests, "get", fake_get)

    entries = figure_official_sources._fetch_berkshire_entries(
        "https://www.berkshirehathaway.com/news/2026news.html"
    )

    assert captured_headers["Accept-Encoding"] == "gzip, deflate"
    assert entries[0]["link"].endswith("news0814.html")
