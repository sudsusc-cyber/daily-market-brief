"""Small, public source packet for QQQM; no credentials or generated values."""

from __future__ import annotations

import logging
import math
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from src.utils.holidays import is_us_market_open

logger = logging.getLogger(__name__)
_BASE = "https://dng-api.invesco.com/cache/v1/accounts/en_US/shareclasses/46138G649"
NAV_URL = _BASE + "/prices?idType=cusip&variationType=priceListing&productType=ETF&productSubType=ETF"
DIV_URL = _BASE + "/distribution?idType=cusip&productType=ETF"
PAIR_URL = "https://historyofmarket.com/api/ndx/forward-pe.json"
PE_URL = "https://www.gurufocus.com/economic_indicators/6778/nasdaq-100-pe-ratio"
PE_READER_URL = "https://r.jina.ai/" + PE_URL
DAILY_FORWARD_BASIS = "dl-blended-fy1fy2"
DIV_BACKUP_URL = "https://stockanalysis.com/etf/qqqm/dividend/"


def latest_closed_date(checked_at: datetime) -> date:
    eastern = checked_at.astimezone(ZoneInfo("America/New_York"))
    anchor = eastern.date()
    if eastern.hour < 16:
        anchor -= timedelta(days=1)
    while not is_us_market_open(anchor):
        anchor -= timedelta(days=1)
    return anchor


def _fetch(url: str) -> dict | None:
    for attempt in range(2):
        try:
            response = requests.get(url, timeout=15)
            response.raise_for_status()
            payload = response.json()
            return payload if isinstance(payload, dict) else None
        except (requests.RequestException, ValueError) as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status is not None and status < 500:
                break  # No retries against access/rate limits.
            if attempt == 0:
                continue
    logger.warning("valuation.qqqm_source_unavailable url=%s", url)
    return None


def _positive(value) -> float:
    if isinstance(value, bool):
        raise ValueError("boolean is not a source value")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("invalid source value") from exc
    if not math.isfinite(number) or number <= 0:
        raise ValueError("invalid source value")
    return number


def parse_gurufocus_pe(text: str, *, anchor: date, reader: bool = False) -> float:
    """Bind the value to its observation date in the same canonical heading."""
    if reader:
        sources = re.findall(r"(?m)^URL Source:\s*(\S+)", text)
        if sources != [PE_URL]:
            raise ValueError("GuruFocus reader canonical source mismatch")
        headings = re.findall(r"(?m)^#\s+(.+)$", text)
    else:
        headings = [node.get_text(" ", strip=True) for node in BeautifulSoup(text, "html.parser").find_all("h1")]
    observations = set()
    for heading in headings:
        match = re.fullmatch(
            r"Nasdaq\s+100\s+PE\s+Ratio\s*:\s*(\d+(?:\.\d+)?)\s*"
            r"\(As\s+of\s+(\d{4}-\d{2}-\d{2})\)", heading.strip(), re.I,
        )
        if match:
            observations.add((_positive(match[1]), date.fromisoformat(match[2])))
    if len(observations) != 1:
        raise ValueError("GuruFocus missing or conflicting dated heading")
    value, observed = observations.pop()
    if observed != anchor:
        raise ValueError(f"GuruFocus observation {observed} differs from NAV date {anchor}")
    return value


def fetch_gurufocus_pe(*, anchor: date) -> dict | None:
    # Two transports, one underlying public source and one valuation definition.
    for url, reader in ((PE_URL, False), (PE_READER_URL, True)):
        try:
            response = requests.get(url, timeout=15)
            response.raise_for_status()
            if not reader and response.url.rstrip("/") != PE_URL.rstrip("/"):
                raise ValueError("GuruFocus redirected away from canonical index page")
            value = parse_gurufocus_pe(response.text, anchor=anchor, reader=reader)
            return {"value": value, "date": anchor.isoformat(), "transport": url}
        except (requests.RequestException, ValueError) as exc:
            logger.info("valuation.qqqm_pe_transport_unavailable reader=%s reason=%s", reader, str(exc)[:120])
    return None


def _series(rows: list, *, daily: bool = False) -> dict[str, float]:
    values: dict[str, float] = {}
    conflicts: set[str] = set()
    for row in rows:
        try:
            if daily and row.get("basis") != DAILY_FORWARD_BASIS:
                continue
            key = date.fromisoformat(row["date"]).isoformat()
            value = _positive(row["value"])
            if key in values and values[key] != value:
                conflicts.add(key)
            values[key] = value
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
    return {key: value for key, value in values.items() if key not in conflicts}


def dividend_rows(payload: dict, *, anchor: date) -> dict[str, float]:
    if payload.get("cusip") != "46138G649" or payload.get("currencyCode") != "USD":
        raise ValueError("QQQM dividend identity/currency mismatch")
    try:
        start = anchor.replace(year=anchor.year - 1)
    except ValueError:
        start = anchor.replace(year=anchor.year - 1, day=28)
    rows = payload.get("distributions", [])
    if not any(date.fromisoformat(row["exDate"]) <= start for row in rows):
        raise ValueError("QQQM dividend history does not cover twelve months")
    selected = [row for row in rows if start < date.fromisoformat(row["exDate"]) <= anchor]
    dates = [row["exDate"] for row in selected]
    if not selected or len(dates) != len(set(dates)):
        raise ValueError("QQQM empty or duplicate dividend rows")
    # QQQM pays quarterly. Missing a whole row must not silently reduce TTM.
    # A future distribution-schedule change requires review, not guessing.
    ordered = sorted(date.fromisoformat(day) for day in dates)
    boundaries = [start, *ordered, anchor]
    if len(ordered) != 4 or any((b - a).days > 110 for a, b in zip(boundaries, boundaries[1:], strict=False)):
        raise ValueError("QQQM quarterly dividend coverage incomplete or schedule changed")
    # Use actual total cash per share in both providers, not a tax-income subset.
    return {row["exDate"]: _positive(row.get("distributionAmountPerUnit")) for row in selected}


def parse_dividend_backup(html: str, *, anchor: date) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    if [h.get_text(" ", strip=True) for h in soup.find_all("h1")] != ["QQQM Dividend Information"]:
        raise ValueError("QQQM backup page identity mismatch")
    text = soup.get_text(" ", strip=True)
    checked = re.search(r"Last checked:\s*([A-Z][a-z]{2} \d{1,2}, \d{4})", text)
    if (not checked or datetime.strptime(checked[1], "%b %d, %Y").date() < anchor
            or not re.search(r"\bUSD\b", text)):
        raise ValueError("QQQM backup freshness/currency unverified")
    candidates = []
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        headers = [cell.get_text(" ", strip=True) for cell in rows[0].find_all("th")]
        if len(headers) != 4 or not headers[0].startswith("Ex-Div") or "Amount" not in headers[1]:
            continue
        parsed = []
        for row in rows[1:]:
            cells = [cell.get_text(" ", strip=True) for cell in row.find_all("td")]
            if len(cells) != 4:
                raise ValueError("QQQM backup dividend row layout changed")
            parsed.append({"exDate": datetime.strptime(cells[0], "%b %d, %Y").date().isoformat(),
                           "distributionAmountPerUnit": _positive(cells[1].removeprefix("$"))})
        candidates.append(parsed)
    if len(candidates) != 1:
        raise ValueError("QQQM backup dividend table missing or ambiguous")
    normalized = {"cusip": "46138G649", "currencyCode": "USD", "distributions": candidates[0]}
    dividend_rows(normalized, anchor=anchor)
    return normalized


def fetch_dividend_backup(*, anchor: date) -> dict | None:
    try:
        response = requests.get(DIV_BACKUP_URL, timeout=15)
        response.raise_for_status()
        return parse_dividend_backup(response.text, anchor=anchor)
    except (requests.RequestException, TypeError, ValueError) as exc:
        logger.info("valuation.qqqm_dividend_backup_unavailable reason=%s", str(exc)[:120])
        return None


def build_source_packet(
    nav: dict, dividends: dict, pair: dict | None, *, checked_at: datetime,
    allow_daily_forward: bool = False,
    dividends_url: str = DIV_URL,
) -> dict:
    """Check fund identity and observation dates before passing evidence to the LLM."""
    anchor = latest_closed_date(checked_at)
    if (nav.get("cusip") != "46138G649" or nav.get("currency") != "USD"
            or dividends.get("cusip") != "46138G649" or dividends.get("currencyCode") != "USD"):
        raise ValueError("QQQM source identity/currency mismatch")
    if nav.get("effectiveDate") != anchor.isoformat():
        raise ValueError("QQQM official NAV is not from latest closed trading day")
    nav_value = _positive(nav.get("nav"))
    if dividends_url not in {DIV_URL, DIV_BACKUP_URL}:
        raise ValueError("QQQM dividend source not approved")
    div_value = sum(dividend_rows(dividends, anchor=anchor).values())
    packet = {
        "data_date": anchor.isoformat(), "nav_anchor": nav_value, "div_ttm": div_value,
        "pe_pair_t": None, "pe_pair_f": None, "fwd_date": None,
        "source_urls": [NAV_URL, dividends_url],
        "citations": [
            {"field": "nav_anchor", "source": NAV_URL, "date": anchor.isoformat(), "quote": str(nav_value)},
            {"field": "div_ttm", "source": dividends_url, "date": anchor.isoformat(), "quote": str(round(div_value, 8))},
        ],
    }
    # Always pair actual dated observations. The daily consensus blend is a
    # separately recorded, opt-in methodology, never silently renamed terminal.
    try:
        raw = pair or {}
        trailing = _series(raw.get("trailing", []))
        candidates = []
        methods = [("forward", "terminal-consensus", False)]
        if allow_daily_forward:
            methods.append(("forwardOwn", DAILY_FORWARD_BASIS, True))
        for field, basis, daily in methods:
            forward = _series(raw.get(field, []), daily=daily)
            common = sorted(d for d in trailing.keys() & forward.keys()
                            if d <= anchor.isoformat() and is_us_market_open(date.fromisoformat(d)))
            for key in reversed(common):
                pair_date = date.fromisoformat(key)
                gap = sum(is_us_market_open(pair_date + timedelta(days=i))
                          for i in range(1, (anchor - pair_date).days + 1))
                t, f = trailing[key], forward[key]
                if gap <= 3 and -0.10 <= t / f - 1 <= 0.40:
                    candidates.append((key, not daily, t, f, basis))
                    break
        if candidates:
            key, _, t, f, basis = max(candidates)  # Newest; terminal wins ties.
            packet.update(pe_pair_t=t, pe_pair_f=f, fwd_date=key, forward_basis=basis)
            packet["source_urls"].append(PAIR_URL)
            packet["citations"].extend(
                {"field": field, "source": PAIR_URL, "date": key, "quote": str(value), "basis": basis}
                for field, value in (("pe_pair_t", t), ("pe_pair_f", f))
            )
        else:
            packet["missing_fields"] = ["pe_pair_t", "pe_pair_f"]
            logger.warning("valuation.qqqm_forward_missing latest_terminal=%s daily_allowed=%s",
                           max(_series(raw.get("forward", [])), default="none"), allow_daily_forward)
    except (KeyError, IndexError, TypeError, ValueError):
        logger.warning("valuation.qqqm_forward_invalid")
    return packet


def fetch_source_packet(*, checked_at: datetime, allow_daily_forward: bool = False) -> dict | None:
    anchor = latest_closed_date(checked_at)
    with ThreadPoolExecutor(max_workers=5) as pool:
        pe_future = pool.submit(fetch_gurufocus_pe, anchor=anchor)
        backup_future = pool.submit(fetch_dividend_backup, anchor=anchor)
        nav, dividends, pair = list(pool.map(_fetch, (NAV_URL, DIV_URL, PAIR_URL)))
        pe = pe_future.result()
        backup = backup_future.result()
    if nav is None:
        return None
    try:
        primary_rows = None
        if dividends is not None:
            try:
                primary_rows = dividend_rows(dividends, anchor=anchor)
            except (KeyError, TypeError, ValueError):
                logger.warning("valuation.qqqm_dividend_primary_invalid")
        dividends_url = DIV_URL
        if primary_rows is None:
            if backup is None:
                return None
            dividends, dividends_url = backup, DIV_BACKUP_URL
            logger.info("valuation.qqqm_dividend_backup_used source=%s", dividends_url)
        elif backup is not None:
            backup_rows = dividend_rows(backup, anchor=anchor)
            if primary_rows.keys() != backup_rows.keys() or any(
                not math.isclose(value, backup_rows[day], rel_tol=1e-8, abs_tol=1e-8)
                for day, value in primary_rows.items()
            ):
                raise ValueError("QQQM 分红主备源逐笔冲突，需核对，不静默选值")
        packet = build_source_packet(nav, dividends, pair, checked_at=checked_at,
                                     allow_daily_forward=allow_daily_forward, dividends_url=dividends_url)
        if pe is not None:
            packet.update(pe_ttm=pe["value"], pe_transport=pe["transport"])
            packet["source_urls"].append(PE_URL)
            packet["citations"].append({"field": "pe_ttm", "source": PE_URL,
                                        "date": pe["date"], "quote": str(pe["value"])})
        return packet
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning("valuation.qqqm_source_packet_invalid reason=%s", exc)
        return None
