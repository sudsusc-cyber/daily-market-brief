"""Small, public source packet for QQQM; no credentials or generated values."""

from __future__ import annotations

import logging
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests

from src.utils.holidays import is_us_market_open

logger = logging.getLogger(__name__)
_BASE = "https://dng-api.invesco.com/cache/v1/accounts/en_US/shareclasses/46138G649"
NAV_URL = _BASE + "/prices?idType=cusip&variationType=priceListing&productType=ETF&productSubType=ETF"
DIV_URL = _BASE + "/distribution?idType=cusip&productType=ETF"
PAIR_URL = "https://historyofmarket.com/api/ndx/forward-pe.json"
PE_URL = "https://www.gurufocus.com/economic_indicators/6778/nasdaq-100-pe-ratio"


def latest_closed_date(checked_at: datetime) -> date:
    eastern = checked_at.astimezone(ZoneInfo("America/New_York"))
    anchor = eastern.date()
    if eastern.hour < 16:
        anchor -= timedelta(days=1)
    while not is_us_market_open(anchor):
        anchor -= timedelta(days=1)
    return anchor


def _fetch(url: str) -> dict | None:
    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else None
    except (requests.RequestException, ValueError):
        logger.warning("valuation.qqqm_source_unavailable url=%s", url)
        return None


def _positive(value) -> float:
    if isinstance(value, bool):
        raise ValueError("boolean is not a source value")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError("invalid source value")
    return number


def build_source_packet(nav: dict, dividends: dict, pair: dict | None, *, checked_at: datetime) -> dict:
    """Check fund identity and observation dates before passing evidence to the LLM."""
    anchor = latest_closed_date(checked_at)
    if (nav.get("cusip") != "46138G649" or nav.get("currency") != "USD"
            or dividends.get("cusip") != "46138G649" or dividends.get("currencyCode") != "USD"):
        raise ValueError("QQQM source identity/currency mismatch")
    if nav.get("effectiveDate") != anchor.isoformat():
        raise ValueError("QQQM official NAV is not from latest closed trading day")
    nav_value = _positive(nav.get("nav"))
    try:
        start = anchor.replace(year=anchor.year - 1)
    except ValueError:  # Feb 29
        start = anchor.replace(year=anchor.year - 1, day=28)
    rows = dividends.get("distributions", [])
    if not any(date.fromisoformat(row["exDate"]) <= start for row in rows):
        raise ValueError("QQQM dividend history does not cover twelve months")
    selected = [row for row in rows if start < date.fromisoformat(row["exDate"]) <= anchor]
    dates = [row["exDate"] for row in selected]
    if not selected or len(dates) != len(set(dates)):
        raise ValueError("QQQM empty or duplicate dividend rows")
    div_value = sum(_positive(row.get("ordinaryIncomeDistribution")) for row in selected)
    packet = {
        "data_date": anchor.isoformat(), "nav_anchor": nav_value, "div_ttm": div_value,
        "pe_pair_t": None, "pe_pair_f": None, "fwd_date": None,
        "source_urls": [NAV_URL, DIV_URL],
        "citations": [
            {"field": "nav_anchor", "source": NAV_URL, "date": anchor.isoformat(), "quote": str(nav_value)},
            {"field": "div_ttm", "source": DIV_URL, "date": anchor.isoformat(), "quote": str(round(div_value, 8))},
        ],
    }
    # Do not confuse a page refresh or the separate forwardOwn series with the
    # observation date of the required same-source trailing/consensus pair.
    try:
        trailing = {r["date"]: _positive(r["value"]) for r in (pair or {}).get("trailing", [])}
        forward = {r["date"]: _positive(r["value"]) for r in (pair or {}).get("forward", [])}
        common = sorted(d for d in trailing.keys() & forward.keys() if d <= anchor.isoformat())
        pair_date = date.fromisoformat(common[-1])
        gap = sum(is_us_market_open(pair_date + timedelta(days=i))
                  for i in range(1, (anchor - pair_date).days + 1))
        t, f = trailing[common[-1]], forward[common[-1]]
        if gap <= 3 and -0.10 <= t / f - 1 <= 0.40:
            packet.update(pe_pair_t=t, pe_pair_f=f, fwd_date=pair_date.isoformat())
            packet["source_urls"].append(PAIR_URL)
            packet["citations"].extend(
                {"field": field, "source": PAIR_URL, "date": pair_date.isoformat(), "quote": str(value)}
                for field, value in (("pe_pair_t", t), ("pe_pair_f", f))
            )
    except (KeyError, IndexError, TypeError, ValueError):
        pass  # Optional B-class input; preserve the core source packet.
    return packet


def fetch_source_packet(*, checked_at: datetime) -> dict | None:
    with ThreadPoolExecutor(max_workers=3) as pool:
        nav, dividends, pair = list(pool.map(_fetch, (NAV_URL, DIV_URL, PAIR_URL)))
    if nav is None or dividends is None:
        return None
    try:
        return build_source_packet(nav, dividends, pair, checked_at=checked_at)
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning("valuation.qqqm_source_packet_invalid reason=%s", exc)
        return None
