from datetime import UTC, date, datetime

import pytest

from src.valuation.qqqm_sources import (
    DAILY_FORWARD_BASIS,
    DIV_BACKUP_URL,
    DIV_URL,
    NAV_URL,
    PE_READER_URL,
    PE_URL,
    build_source_packet,
    fetch_gurufocus_pe,
    fetch_source_packet,
    latest_closed_date,
    parse_dividend_backup,
    parse_gurufocus_pe,
)

NOW = datetime(2026, 9, 3, 4, tzinfo=UTC)


def _sources():
    nav = {"cusip": "46138G649", "currency": "USD", "nav": 292.029084,
           "effectiveDate": "2026-09-02"}
    dividends = {"cusip": "46138G649", "currencyCode": "USD", "distributions": [
        {"exDate": d, "distributionAmountPerUnit": v}
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


def _backup_html():
    rows = (("Jun 22, 2026", ".35215"), ("Mar 23, 2026", ".32769"),
            ("Dec 22, 2025", ".32301"), ("Sep 22, 2025", ".30245"), ("Jun 23, 2025", ".3161"))
    return ('<h1>QQQM Dividend Information</h1><p>USD Last checked: Sep 2, 2026</p>'
            '<table><tr><th>Ex-Dividend Date</th><th>Cash Amount</th><th>Record Date</th><th>Pay Date</th></tr>'
            + ''.join(f'<tr><td>{day}</td><td>${amount}</td><td>x</td><td>y</td></tr>' for day, amount in rows)
            + '</table>')


def test_independent_dividend_backup_uses_exact_rows_not_rounded_summary():
    backup = parse_dividend_backup(_backup_html() + '<p>Annual dividend $1.31</p>', anchor=date(2026, 9, 2))
    packet = build_source_packet(_sources()[0], backup, None, checked_at=NOW, dividends_url=DIV_BACKUP_URL)
    assert packet["div_ttm"] == pytest.approx(1.3053)
    assert packet["citations"][1]["source"] == DIV_BACKUP_URL


@pytest.mark.parametrize("old,new", [("QQQM Dividend", "QQQ Dividend"), ("USD", "HKD"),
                                    ("Sep 2, 2026", "Aug 31, 2026"),
                                    (".35215", "NaN"), ("Jun 23, 2025", "Jun 23, 2026"),
                                    ("Sep 22, 2025", "Jun 22, 2026")])
def test_backup_rejects_wrong_identity_stale_incomplete_duplicate_or_bad_numbers(old, new):
    with pytest.raises(ValueError):
        parse_dividend_backup(_backup_html().replace(old, new), anchor=date(2026, 9, 2))


@pytest.mark.parametrize("primary_mode", ["missing", "invalid", "matching", "conflict"])
def test_source_packet_backup_recovery_and_cross_check(monkeypatch, primary_mode):
    import copy

    nav, primary = _sources()
    backup = copy.deepcopy(primary)
    if primary_mode == "missing":
        primary = None
    elif primary_mode == "invalid":
        primary["currencyCode"] = "HKD"
    elif primary_mode == "conflict":
        primary["distributions"][0]["distributionAmountPerUnit"] = .4
    monkeypatch.setattr("src.valuation.qqqm_sources._fetch",
                        lambda url: nav if url == NAV_URL else primary if url == DIV_URL else None)
    monkeypatch.setattr("src.valuation.qqqm_sources.fetch_gurufocus_pe", lambda **kwargs: None)
    monkeypatch.setattr("src.valuation.qqqm_sources.fetch_dividend_backup", lambda **kwargs: backup)
    packet = fetch_source_packet(checked_at=NOW)
    if primary_mode == "conflict":
        assert packet is None
    else:
        assert packet["div_ttm"] == pytest.approx(1.3053)
        assert packet["citations"][1]["source"] == (DIV_URL if primary_mode == "matching" else DIV_BACKUP_URL)


@pytest.mark.parametrize("missing", ["Jun 22, 2026", "Mar 23, 2026"])
def test_dividend_backup_rejects_missing_whole_quarter(missing):
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(_backup_html(), "html.parser")
    for row in soup.find_all("tr"):
        if missing in row.get_text():
            row.decompose()
    with pytest.raises(ValueError, match="coverage"):
        parse_dividend_backup(str(soup), anchor=date(2026, 9, 2))


def test_primary_uses_total_cash_not_ordinary_income_tax_subset():
    nav, dividends = _sources()
    dividends["distributions"][0]["ordinaryIncomeDistribution"] = .2
    assert build_source_packet(nav, dividends, None, checked_at=NOW)["div_ttm"] == pytest.approx(1.3053)
