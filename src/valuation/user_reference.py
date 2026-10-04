"""Explicit user references remain separate from source-verified research."""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path

from src.utils.dates import to_beijing
from src.valuation.models import ValuationDisplay
from src.valuation.morningstar import (
    SECURITIES,
    MorningstarFairValue,
    _validate_live,
    _valuation_time,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UserFairValueReference:
    ticker: str
    fair_value: float
    currency: str
    provided_on: date


def load_user_references(config_dir: Path, *, checked_at: datetime) -> list[UserFairValueReference]:
    path = config_dir / "user_fair_value_references.json"
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("schema_version") != "1.0":
            raise ValueError("invalid schema")
        rows = payload.get("references")
        if not isinstance(rows, list):
            raise ValueError("invalid references")
    except (OSError, ValueError) as exc:
        logger.error("valuation.user_reference_invalid reason=%s", exc)
        return []
    references = {}
    for row in rows:
        try:
            security = SECURITIES[row["ticker"]]
            if (row["provider_code"], row["currency"]) != (security.provider_code, security.currency):
                raise ValueError("listing or currency mismatch")
            if (row["claimed_provider"] != "Morningstar"
                    or row["replace_when"] != "verified_valuation_date_strictly_newer"):
                raise ValueError("invalid replacement policy")
            amount = row["fair_value"]
            if isinstance(amount, bool) or not isinstance(amount, (int, float)) or not math.isfinite(amount) or amount <= 0:
                raise ValueError("invalid amount")
            provided_on = date.fromisoformat(row["provided_on"])
            if provided_on > to_beijing(checked_at).date():
                continue
            reference = UserFairValueReference(security.ticker, float(amount), security.currency, provided_on)
            previous = references.get(security.ticker)
            if previous is None or provided_on > previous.provided_on:
                references[security.ticker] = reference
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            logger.error("valuation.user_reference_row_invalid reason=%s", exc)
    return list(references.values())


def apply_user_references(
    displays: dict[str, ValuationDisplay],
    *,
    fair_values: Mapping[str, MorningstarFairValue],
    prices: Mapping[str, float | None],
    config_dir: Path,
    checked_at: datetime,
) -> dict[str, ValuationDisplay]:
    updated = dict(displays)
    for reference in load_user_references(config_dir, checked_at=checked_at):
        ticker = reference.ticker
        if ticker not in displays:
            continue
        researched = fair_values.get(ticker)
        if researched is not None:
            try:
                if not researched.extraction_verified:
                    raise ValueError("research amount was not extracted and verified")
                _validate_live(researched, previous=None, current_price=None,
                    checked_at=checked_at, ticker=ticker)
                if _valuation_time(researched).date() > reference.provided_on:
                    continue
            except (ValueError, TypeError, KeyError):
                pass
        price = prices.get(ticker)
        implied = (reference.fair_value / price - 1
            if isinstance(price, (int, float)) and not isinstance(price, bool)
            and math.isfinite(price) and price > 0 else None)
        day = reference.provided_on.isoformat()
        updated[ticker] = replace(
            displays[ticker], status="not_due", intrinsic_value=reference.fair_value,
            implied_return=implied, hurdle_rate=0.10,
            currency_symbol={"USD": "$", "HKD": "HK$"}[reference.currency],
            financial_as_of=None, approved_at=day, verified_at=None, source_url=None,
            source_document_id=f"user-reference:{ticker}:{day}:{reference.fair_value:g}",
            formula_id="fair_value_price_gap", model_version="user-reference-v1",
            return_label="IRR", value_label="公允价值", historical_reference=False,
            reference_origin="user_provided", warnings=(),
            data_note=f"{ticker} 采用用户指定参考 {reference.fair_value:g} {reference.currency}（{day}）",
        )
        logger.info("valuation.user_reference_applied ticker=%s value=%s provided_on=%s",
            ticker, reference.fair_value, day)
    return updated
