from datetime import UTC, datetime

import pytest

from src.valuation.qqqm_sources import build_source_packet, latest_closed_date

NOW = datetime(2026, 9, 3, 4, tzinfo=UTC)


def _sources():
    nav = {"cusip": "46138G649", "currency": "USD", "nav": 292.029084,
           "effectiveDate": "2026-09-02"}
    dividends = {"cusip": "46138G649", "currencyCode": "USD", "distributions": [
        {"exDate": d, "ordinaryIncomeDistribution": v}
        for d, v in (("2026-06-22", .35215), ("2026-03-23", .32769),
                     ("2025-12-22", .32301), ("2025-09-22", .30245), ("2025-06-23", .3161))
    ]}
    return nav, dividends


def test_live_packet_uses_fund_nav_and_trailing_dividends():
    nav, dividends = _sources()
    result = build_source_packet(nav, dividends, None, checked_at=NOW)
    assert result["data_date"] == "2026-09-02"
    assert result["div_ttm"] == pytest.approx(1.3053)
    assert result["nav_anchor"] == 292.029084
    assert result["pe_pair_t"] is None


def test_pair_uses_observation_dates_not_page_refresh():
    nav, dividends = _sources()
    pair = {"updated": "2026-09-02", "current": {"trailing": 28, "forward": 22},
            "trailing": [{"date": "2026-08-05", "value": 28}],
            "forward": [{"date": "2026-08-05", "value": 22}]}
    assert build_source_packet(nav, dividends, pair, checked_at=NOW)["fwd_date"] is None
    for key in ("trailing", "forward"):
        pair[key][0]["date"] = "2026-08-31"
    result = build_source_packet(nav, dividends, pair, checked_at=NOW)
    assert result["fwd_date"] == "2026-08-31"
    assert result["pe_pair_t"] == 28


def test_reject_wrong_fund_stale_nav_duplicate_dividends():
    nav, dividends = _sources()
    with pytest.raises(ValueError, match="identity"):
        build_source_packet(dict(nav, cusip="QQQ"), dividends, None, checked_at=NOW)
    with pytest.raises(ValueError, match="latest closed"):
        build_source_packet(dict(nav, effectiveDate="2026-09-01"), dividends, None, checked_at=NOW)
    dividends["distributions"].append(dividends["distributions"][0])
    with pytest.raises(ValueError, match="duplicate"):
        build_source_packet(nav, dividends, None, checked_at=NOW)


def test_closed_date_handles_weekend_holiday_and_intraday():
    assert latest_closed_date(NOW).isoformat() == "2026-09-02"
    assert latest_closed_date(datetime(2026, 9, 7, 23, tzinfo=UTC)).isoformat() == "2026-09-04"
    assert latest_closed_date(datetime(2026, 9, 3, 18, tzinfo=UTC)).isoformat() == "2026-09-02"
