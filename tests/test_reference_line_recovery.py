"""Sep 29 regression: long Yahoo windows have null final bars, dated query works."""
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from src.collectors import stocks
from src.config import HOLDINGS, buy_strategy
from src.renderer.render import render_email
from src.utils.market_clock import calendar, latest_closed_session

NOW = datetime(2026, 9, 29, 0, 30, tzinfo=UTC)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz or UTC)
    monkeypatch.setattr(stocks, "datetime", Clock)
    monkeypatch.setattr(stocks.requests, "get", lambda *a, **k: pytest.fail("unexpected network"))


def identity(symbol):
    return {"symbol": symbol, "currency": "HKD" if symbol.endswith(".HK") else "USD",
            "exchangeName": "HKG" if symbol.endswith(".HK") else "NMS",
            "regularMarketPrice": 999999}  # Never borrow this price.


def daily_result(symbol="MSFT", now=NOW):
    cal = calendar(symbol, now.year)
    day = latest_closed_session(symbol, now)
    return {"meta": identity(symbol), "timestamp": [int(cal.session_close(str(day)).timestamp())],
            "indicators": {"quote": [{"close": [509.22]}]}}


def response(result):
    return SimpleNamespace(status_code=200, json=lambda: {"chart": {"result": [result]}})


def history(symbol, interval, *, now=NOW):
    cal = calendar(symbol, now.year)
    day = latest_closed_session(symbol, now)
    if interval == "1d":
        index = cal.sessions_in_range(str(day - timedelta(days=500)), str(day))[-300:]
        index = index.tz_localize(cal.tz)
    else:
        monday = day - timedelta(days=day.weekday())
        index = pd.date_range(end=monday, periods=220, freq="W-MON", tz=cal.tz)
    return pd.DataFrame({"Close": [100.0] * (len(index) - 1) + [float("nan")]}, index=index)


@pytest.mark.parametrize("holding", HOLDINGS, ids=lambda h: h.ticker)
def test_complete_holding_signal_recovers_only_final_daily_and_weekly_bar(monkeypatch, holding):
    symbol = holding.yfinance_symbol
    ticker = SimpleNamespace(_price_history=SimpleNamespace(_history_metadata=identity(symbol)))
    monkeypatch.setattr(stocks.yf, "Ticker", lambda _: ticker)
    monkeypatch.setattr(stocks, "_yf_daily_history", lambda _: history(symbol, "1d"))
    monkeypatch.setattr(stocks, "_yf_history", lambda _: history(symbol, "1wk"))
    requests = []
    def get(url, **kwargs):
        requests.append(kwargs["params"])
        return response(daily_result(symbol))
    monkeypatch.setattr(stocks.requests, "get", get)
    signal = stocks.fetch_one(holding)
    assert signal.error is None
    assert signal.last_close == 509.22
    assert signal.sma_120 == pytest.approx((119 * 100 + 509.22) / 120)
    assert signal.sma_200 == pytest.approx((199 * 100 + 509.22) / 200)
    if buy_strategy(holding.ticker).dca_line == "250d":
        assert signal.sma_250d == pytest.approx((249 * 100 + 509.22) / 250)
    assert signal.observed_at == "2026-09-28"
    assert "daily_recovery:yahoo_session" in signal.data_source
    assert "weekly_recovery:yahoo_session" in signal.data_source
    assert len(requests) == 2
    assert all(p["interval"] == "1d" and "range" not in p and p["includePrePost"] == "false"
               for p in requests)
    assert all(line["value"] is not None for line in signal.reference_lines)


@pytest.mark.parametrize("symbol,now", [
    ("MSFT", NOW),
    ("MSFT", datetime(2026, 9, 7, 23, tzinfo=UTC)),  # Labor Day -> Friday
    ("MSFT", datetime(2026, 11, 27, 19, tzinfo=UTC)),  # US half day
    ("0700.HK", datetime(2026, 12, 24, 5, tzinfo=UTC)),  # HK half day
    ("0700.HK", datetime(2026, 7, 1, 23, tzinfo=UTC)),  # HK holiday
    ("MSFT", datetime(2026, 9, 28, 15, tzinfo=UTC)),  # Intraday -> Friday
])
def test_targeted_query_is_exact_local_closed_session(monkeypatch, symbol, now):
    seen = []
    def get(url, **kwargs):
        seen.append(kwargs["params"])
        return response(daily_result(symbol, now))
    monkeypatch.setattr(stocks.requests, "get", get)
    values = stocks._yahoo_session_close(symbol, now=now)
    expected = latest_closed_session(symbol, now)
    cal = calendar(symbol, now.year)
    start = pd.Timestamp(expected, tz=cal.tz)
    assert seen[0]["period1"] == int(start.timestamp())
    assert seen[0]["period2"] == int((start + pd.DateOffset(days=1)).timestamp())
    assert values.observed_at == str(expected)
    assert values == [509.22]


@pytest.mark.parametrize("bad", ["symbol", "currency", "exchange", "old", "intraday",
                                 "duplicate", "null", "zero", "negative", "infinite", "boolean"])
def test_recovery_rejects_invalid_target_response(monkeypatch, bad):
    result = daily_result()
    if bad in {"symbol", "currency", "exchange"}:
        key = {"symbol": "symbol", "currency": "currency", "exchange": "exchangeName"}[bad]
        result["meta"][key] = "WRONG"
    elif bad == "old":
        result["timestamp"] = [int(datetime(2026, 9, 25, 20, tzinfo=UTC).timestamp())]
    elif bad == "intraday":  # Current session is not closed yet.
        result["timestamp"] = [int(datetime(2026, 9, 29, 13, 31, tzinfo=UTC).timestamp())]
    elif bad == "duplicate":
        result["timestamp"] *= 2
        result["indicators"]["quote"][0]["close"] *= 2
    else:
        result["indicators"]["quote"][0]["close"] = [{
            "null": None, "zero": 0, "negative": -1, "infinite": float("inf"), "boolean": True}[bad]]
    monkeypatch.setattr(stocks.requests, "get", lambda *a, **k: response(result))
    with pytest.raises(ValueError):
        stocks._history_closes(history("MSFT", "1d"), symbol="MSFT", interval="1d")


@pytest.mark.parametrize("bad", ["middle_null", "middle_missing", "short", "stale", "duplicate"])
@pytest.mark.parametrize("interval", ["1d", "1wk"])
def test_recovery_cannot_hide_bad_active_window(monkeypatch, bad, interval):
    hist = history("NVDA", interval)
    if bad == "middle_null":
        hist.iloc[-10, 0] = float("nan")
    elif bad == "middle_missing":
        hist = hist.drop(hist.index[-10])
    elif bad == "short":
        hist = hist.iloc[-2:]
    elif bad == "stale":
        hist = hist.iloc[:-1]
    elif bad == "duplicate":
        hist.index = pd.DatetimeIndex([*hist.index[:-2], hist.index[-1], hist.index[-1]])
    with pytest.raises(ValueError):
        stocks._history_closes(hist, symbol="NVDA", interval=interval)


@pytest.mark.parametrize("interval", ["1d", "1wk"])
def test_direct_chart_fallback_also_recovers_final_bar(monkeypatch, interval):
    hist = history("NVDA", interval)
    result = {"meta": identity("NVDA"),
              "timestamp": [int(t.timestamp()) for t in hist.index],
              "indicators": {"quote": [{"close": [*hist.Close.iloc[:-1], None]}]}}
    calls = []
    def get(url, **kwargs):
        calls.append(kwargs["params"])
        return response(deepcopy(result) if kwargs["params"]["period2"] - kwargs["params"]["period1"] > 86400 else daily_result("NVDA"))
    monkeypatch.setattr(stocks.requests, "get", get)
    closes, last = stocks._yahoo_chart_history("NVDA", interval=interval, period="2y", minimum=120)
    assert closes[-1] == 509.22
    assert list(closes[:-1]) == list(hist.Close.iloc[:-1])
    assert closes.bar_date == "2026-09-28"
    assert closes.observed_at == "2026-09-28"
    assert last == (509.22 if interval == "1d" else None)
    assert len(calls) == 2


def test_valid_history_does_not_request_recovery():
    hist = history("NVDA", "1d")
    hist.iloc[-1, 0] = 500
    closes = stocks._history_closes(hist, symbol="NVDA", interval="1d")
    assert closes[-1] == 500
    assert not closes.recovery_source


def test_daily_failure_keeps_both_diagnostics(monkeypatch, caplog):
    def fail_primary(_):
        raise ValueError("original missing close expected_date=2026-09-28")
    def fail_backup(_):
        raise ValueError("target day HTTP 503")
    monkeypatch.setattr(stocks, "_yf_verified_daily", fail_primary)
    monkeypatch.setattr(stocks, "_yahoo_chart_daily", fail_backup)
    signal = stocks.fetch_one(HOLDINGS[0])
    assert "original missing close expected_date=2026-09-28" in signal.error
    assert "target day HTTP 503" in signal.error
    assert "original missing close" in caplog.text


def test_actual_september29_responses_restore_reference_lines_in_email(monkeypatch):
    # Public Yahoo response captured after the formal run: long windows end
    # in null, while the exact-session query returns the Sep 28 Close.
    fixture = json.loads((Path(__file__).parent / "fixtures/reference_line_null_tail.json").read_text())
    def get(url, **kwargs):
        params = kwargs["params"]
        key = ("daily" if params["interval"] == "1d" else "weekly") if params["period2"] - params["period1"] > 86400 else "target"
        return response(deepcopy(fixture[key]))
    monkeypatch.setattr(stocks.requests, "get", get)
    def primary_unavailable(*args):
        raise ValueError("fixture primary unavailable")
    monkeypatch.setattr(stocks, "_yf_verified_daily", primary_unavailable)
    monkeypatch.setattr(stocks, "_yf_history", primary_unavailable)
    monkeypatch.setattr(stocks.yf, "Ticker", lambda _: SimpleNamespace())
    signal = stocks.fetch_one(next(h for h in HOLDINGS if h.ticker == "MSFT"))
    assert signal.error is None
    assert signal.last_close == pytest.approx(509.22)
    assert signal.sma_120 == pytest.approx(444.2277488708496)
    assert signal.sma_200 == pytest.approx(402.7998497009277)
    assert signal.observed_at == "2026-09-28"
    html = render_email(signals=[signal], generated_at=NOW)
    assert "数据获取失败" not in html
    assert "444.23" in html and "402.80" in html
