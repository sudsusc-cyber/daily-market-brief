from datetime import UTC, date, datetime

import pytest

from src.valuation.qqqm_sources import (
    DAILY_FORWARD_BASIS,
    PE_READER_URL,
    PE_URL,
    build_source_packet,
    fetch_gurufocus_pe,
    latest_closed_date,
    parse_gurufocus_pe,
)

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


def test_gurufocus_value_and_date_come_from_same_heading():
    html = "<h1>Nasdaq 100 PE Ratio : <span>28.28</span> (As of 2026-09-02)</h1>"
    html += "<p>Last Value 28.22 Latest Period 2026-09-01</p>"
    assert parse_gurufocus_pe(html, anchor=date(2026, 9, 2)) == 28.28
    text = f"URL Source: {PE_URL}\n# Nasdaq 100 PE Ratio : 28.28 (As of 2026-09-02)\n"
    assert parse_gurufocus_pe(text, anchor=date(2026, 9, 2), reader=True) == 28.28
    with pytest.raises(ValueError, match="canonical"):
        parse_gurufocus_pe(text.replace(PE_URL, "https://example.com"), anchor=date(2026, 9, 2), reader=True)
    with pytest.raises(ValueError, match="differs"):
        parse_gurufocus_pe(html, anchor=date(2026, 9, 3))
    with pytest.raises(ValueError, match="conflicting"):
        parse_gurufocus_pe(html + html.replace("28.28", "28.29"), anchor=date(2026, 9, 2))


def test_gurufocus_same_page_reader_recovers_direct_403(monkeypatch):
    import requests

    calls = []

    def get(url, **kwargs):
        calls.append(url)
        response = requests.Response()
        response.status_code = 403 if url == PE_URL else 200
        response._content = (f"URL Source: {PE_URL}\n# Nasdaq 100 PE Ratio : 28.28 (As of 2026-09-02)").encode()
        return response

    monkeypatch.setattr("src.valuation.qqqm_sources.requests.get", get)
    result = fetch_gurufocus_pe(anchor=date(2026, 9, 2))
    assert result == {"value": 28.28, "date": "2026-09-02", "transport": PE_READER_URL}
    assert calls == [PE_URL, PE_READER_URL]


def test_opt_in_daily_consensus_uses_dated_pair_and_records_basis():
    nav, dividends = _sources()
    pair = {
        "updated": "2026-09-03", "current": {"trailing": 999, "forwardOwn": 999},
        "trailing": [{"date": "2026-08-05", "value": 28},
                     {"date": "2026-09-02", "value": 28.06},
                     {"date": "2026-09-03", "value": 27.79}],
        "forward": [{"date": "2026-08-05", "value": 22.37}],
        "forwardOwn": [{"date": "2026-09-02", "value": 21.21, "basis": DAILY_FORWARD_BASIS},
                       {"date": "2026-09-03", "value": 20, "basis": DAILY_FORWARD_BASIS}],
    }
    assert build_source_packet(nav, dividends, pair, checked_at=NOW)["pe_pair_f"] is None
    result = build_source_packet(nav, dividends, pair, checked_at=NOW, allow_daily_forward=True)
    assert result["pe_pair_t"] == 28.06
    assert result["pe_pair_f"] == 21.21
    assert result["fwd_date"] == "2026-09-02"
    assert result["forward_basis"] == DAILY_FORWARD_BASIS
    pair["forwardOwn"][0]["basis"] = "unknown-new-method"
    assert build_source_packet(nav, dividends, pair, checked_at=NOW, allow_daily_forward=True)["pe_pair_f"] is None


def test_pair_ignores_bad_history_but_rejects_conflicting_current_values():
    nav, dividends = _sources()
    pair = {"trailing": [{"date": "bad", "value": None}, {"date": "2026-09-02", "value": 28}],
            "forward": [{"date": "2026-09-02", "value": 22}]}
    assert build_source_packet(nav, dividends, pair, checked_at=NOW)["pe_pair_t"] == 28
    pair["forward"].append({"date": "2026-09-02", "value": 23})
    assert build_source_packet(nav, dividends, pair, checked_at=NOW)["pe_pair_t"] is None


def test_terminal_pair_preferred_on_equal_date_but_newer_daily_can_win():
    nav, dividends = _sources()
    pair = {"trailing": [{"date": "2026-09-01", "value": 28}, {"date": "2026-09-02", "value": 28}],
            "forward": [{"date": "2026-09-02", "value": 22}],
            "forwardOwn": [{"date": "2026-09-02", "value": 21.5, "basis": DAILY_FORWARD_BASIS}]}
    result = build_source_packet(nav, dividends, pair, checked_at=NOW, allow_daily_forward=True)
    assert result["pe_pair_f"] == 22
    assert result["forward_basis"] == "terminal-consensus"
    pair["forward"][0]["date"] = "2026-09-01"
    result = build_source_packet(nav, dividends, pair, checked_at=NOW, allow_daily_forward=True)
    assert result["pe_pair_f"] == 21.5


def test_weekend_pair_is_not_a_closed_trading_observation():
    nav, dividends = _sources()
    pair = {"trailing": [{"date": "2026-08-30", "value": 28}],
            "forward": [{"date": "2026-08-30", "value": 22}]}
    assert build_source_packet(nav, dividends, pair, checked_at=NOW)["pe_pair_f"] is None
