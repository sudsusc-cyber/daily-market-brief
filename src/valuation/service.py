"""估值 v1 在主流程中的编排入口。"""

from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.collectors.stocks import StockSignal
from src.valuation.engine import ValuationInputError, calculate_display, load_snapshots
from src.valuation.freshness import check_official_freshness
from src.valuation.models import FreshnessResult, ValuationDisplay
from src.valuation.policy import POLICIES

logger = logging.getLogger(__name__)

_PUBLISHED_STATE_NAME = "valuation_published.json"
_MAX_UNATTRIBUTED_MOVE = 0.03


def _symbol(currency: str) -> str:
    return {"USD": "$", "HKD": "HK$", "CNY": "¥", "TWD": "NT$"}.get(currency, currency)


def _load_published_state(state_dir: Path) -> dict[str, dict[str, Any]]:
    path = state_dir / _PUBLISHED_STATE_NAME
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("valuation.published_state_invalid reason=%s", exc)
        return {}
    rows = payload.get("valuations") if isinstance(payload, dict) else None
    if not isinstance(rows, dict):
        return {}
    return {str(ticker): row for ticker, row in rows.items() if isinstance(row, dict)}


def enforce_jump_guard(
    displays: dict[str, ValuationDisplay],
    *,
    state_dir: Path,
    threshold: float = _MAX_UNATTRIBUTED_MOVE,
) -> dict[str, ValuationDisplay]:
    """阻断无新财报、无模型升级却单日跳变超过阈值的内在价值。"""
    previous = _load_published_state(state_dir)
    guarded = dict(displays)
    for ticker, display in displays.items():
        old = previous.get(ticker)
        if display.intrinsic_value is None or not old:
            continue
        try:
            old_value = float(old["intrinsic_value"])
        except (KeyError, TypeError, ValueError):
            continue
        same_evidence = (
            old.get("source_document_id") == display.source_document_id
            and old.get("model_version") == display.model_version
        )
        if not same_evidence or old_value <= 0 or not math.isfinite(old_value):
            continue
        move = abs(display.intrinsic_value / old_value - 1)
        if move <= threshold:
            continue
        guarded[ticker] = replace(
            display,
            status="manual_review",
            intrinsic_value=None,
            implied_return=None,
            warnings=(f"同一官方文件与模型下内在价值跳变 {move:.1%}，待人工核验",),
        )
        logger.warning(
            "valuation.jump_blocked ticker=%s old=%.4f new=%.4f move=%.4f",
            ticker,
            old_value,
            display.intrinsic_value,
            move,
        )
    return guarded


def commit_published_values(
    displays: dict[str, ValuationDisplay] | None,
    *,
    state_dir: Path,
    sent_at: datetime,
) -> None:
    """只在 SMTP 成功后记录本次真正展示的估值，供下次跳变检查。"""
    if not displays:
        return
    previous = _load_published_state(state_dir)
    for ticker, display in displays.items():
        if display.intrinsic_value is None:
            continue
        previous[ticker] = {
            "intrinsic_value": display.intrinsic_value,
            "implied_return": display.implied_return,
            "source_document_id": display.source_document_id,
            "model_version": display.model_version,
            "sent_at": sent_at.astimezone(UTC).isoformat(),
        }
    path = state_dir / _PUBLISHED_STATE_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps({"valuations": previous}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(tmp, path)


def prepare_valuation_displays(
    *,
    signals: list[StockSignal],
    state_dir: Path,
    config_dir: Path,
    checked_at: datetime | None = None,
    download_original: bool = True,
    prior_freshness: dict[str, FreshnessResult] | None = None,
) -> tuple[dict[str, ValuationDisplay], dict[str, FreshnessResult]]:
    """读取批准底稿、核验最新文件并由 Python 计算邮件展示值。"""
    if checked_at is None:
        checked_at = datetime.now(UTC)
    try:
        snapshots = load_snapshots(
            state_dir / "valuation_snapshots.json",
            config_dir / "valuation_snapshots.json",
        )
    except ValuationInputError as exc:
        logger.error("valuation.snapshots_invalid reason=%s", exc)
        snapshots = {}

    accepted_ids = {
        ticker: snapshot.source_document_id for ticker, snapshot in snapshots.items()
    }
    freshness = check_official_freshness(
        POLICIES.values(),
        valuation_document_ids=accepted_ids,
        checked_at=checked_at,
        download_original=download_original,
        prior_results=prior_freshness,
    )
    prices = {signal.holding.ticker: signal.last_close for signal in signals}
    displays: dict[str, ValuationDisplay] = {}
    for ticker, policy in POLICIES.items():
        snapshot = snapshots.get(ticker)
        result = freshness[ticker]
        if snapshot is None:
            displays[ticker] = ValuationDisplay(
                ticker=ticker,
                status=result.status,
                hurdle_rate=policy.hurdle_rate,
                currency_symbol=_symbol(policy.market_currency),
                formula_id=policy.formula_id,
                model_version=policy.model_version,
                warnings=(result.reason or "尚无经过批准的估值底稿",),
            )
            continue
        displays[ticker] = calculate_display(
            policy=policy,
            snapshot=snapshot,
            freshness=result,
            current_price=prices.get(ticker),
        )
    displays = enforce_jump_guard(displays, state_dir=state_dir)
    current = sum(1 for value in displays.values() if not value.is_pending)
    logger.info(
        "valuation.prepared current=%d pending=%d checked_at=%s",
        current,
        len(displays) - current,
        checked_at.isoformat(),
    )
    return displays, freshness
