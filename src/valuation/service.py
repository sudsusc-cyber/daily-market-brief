"""估值 v2 在主流程中的编排入口。"""

from __future__ import annotations

import json
import logging
import math
import os
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.collectors.stocks import StockSignal
from src.valuation.autosnapshot import refresh_snapshots
from src.valuation.engine import ValuationInputError, calculate_display, load_snapshots
from src.valuation.freshness import check_official_freshness, evaluate_freshness
from src.valuation.models import FreshnessResult, OfficialDocument, ValuationDisplay
from src.valuation.morningstar import (
    MorningstarFairValue,
    MorningstarProvider,
    refresh_fair_values,
)
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


def apply_morningstar_fair_values(
    displays: dict[str, ValuationDisplay],
    *,
    fair_values: Mapping[str, MorningstarFairValue],
    failures: Mapping[str, str],
    prices: Mapping[str, float | None],
) -> dict[str, ValuationDisplay]:
    """用晨星公允价值与现价统一生成 1Y IRR，不沿用旧模型 IRR。"""
    updated = dict(displays)
    for ticker, display in displays.items():
        fair_value = fair_values.get(ticker)
        if fair_value is None:
            reason = failures.get(ticker, "公开 Morningstar 来源本次不可用")
            updated[ticker] = replace(
                display,
                status="source_unavailable",
                intrinsic_value=None,
                implied_return=None,
                hurdle_rate=0.10,
                return_label="IRR",
                value_label="公允价值",
                warnings=(*display.warnings, reason),
            )
            continue
        current_price = prices.get(ticker)
        implied_return = (
            fair_value.fair_value / current_price - 1
            if current_price is not None and current_price > 0
            else None
        )
        warning = (fair_value.warning,) if fair_value.warning else ()
        updated[ticker] = replace(
            display,
            status="not_due" if fair_value.stale_cache else "current",
            verified_at=fair_value.retrieved_at,
            data_note=(f"{ticker} 沿用 {fair_value.retrieved_at[:10]} 核验值" if fair_value.stale_cache else None),
            intrinsic_value=fair_value.fair_value,
            implied_return=implied_return,
            hurdle_rate=0.10,
            currency_symbol=_symbol(fair_value.currency),
            financial_as_of=fair_value.fair_value_updated_at,
            source_url=fair_value.source_url,
            source_document_id=(
                f"morningstar:{fair_value.provider_code}:"
                f"{fair_value.fair_value_updated_at}:{fair_value.fair_value:g}"
            ),
            formula_id="morningstar_fair_value_1y_irr",
            model_version="morningstar-public-v2",
            return_label="IRR",
            value_label="公允价值",
            warnings=(*display.warnings, *warning),
        )
    return updated


def prepare_valuation_displays(
    *,
    signals: list[StockSignal],
    state_dir: Path,
    config_dir: Path,
    checked_at: datetime | None = None,
    download_original: bool = True,
    prior_freshness: dict[str, FreshnessResult] | None = None,
    reviewer: Any | None = None,
    morningstar_provider: MorningstarProvider | None = None,
    qqqm_display: ValuationDisplay | None = None,
) -> tuple[dict[str, ValuationDisplay], dict[str, FreshnessResult]]:
    """自动刷新底稿、核验最新文件并由 Python 计算邮件展示值。"""
    if checked_at is None:
        checked_at = datetime.now(UTC)
    prices = {signal.holding.ticker: signal.last_close for signal in signals}

    # 晨星模式的展示值只依赖公开公允价值与现价。旧的逐股 DCF/SOTP 底稿
    # 最终会被完整覆盖，因此不再下载财报或调用 DeepSeek 做 14 次无效复核。
    if morningstar_provider is not None:
        displays = {
            ticker: ValuationDisplay(
                ticker=ticker,
                status="current",
                hurdle_rate=0.10,
                currency_symbol=_symbol(policy.market_currency),
                return_label="IRR",
                value_label="公允价值",
            )
            for ticker, policy in POLICIES.items()
        }
        fair_values, fair_value_failures = refresh_fair_values(
            provider=morningstar_provider,
            state_dir=state_dir,
            prices=prices,
            checked_at=checked_at,
        )
        displays = apply_morningstar_fair_values(
            displays,
            fair_values=fair_values,
            failures=fair_value_failures,
            prices=prices,
        )
        if qqqm_display is not None:
            displays["QQQM"] = qqqm_display
        displays = enforce_jump_guard(displays, state_dir=state_dir)
        current = sum(1 for value in displays.values() if not value.is_pending)
        logger.info(
            "valuation.morningstar_fast_path skipped_internal_reviews=%d",
            len(POLICIES),
        )
        for ticker, value in displays.items():
            logger.info(
                "valuation.display ticker=%s status=%s intrinsic=%s implied_return=%s source=%s",
                ticker,
                value.status,
                f"{value.intrinsic_value:.4f}" if value.intrinsic_value is not None else "none",
                f"{value.implied_return:.6f}" if value.implied_return is not None else "none",
                value.source_document_id or "none",
            )
        logger.info(
            "valuation.prepared current=%d pending=%d checked_at=%s",
            current,
            len(displays) - current,
            checked_at.isoformat(),
        )
        return displays, {}

    try:
        snapshots = load_snapshots(
            state_dir / "valuation_snapshots.json",
            config_dir / "valuation_snapshots.json",
        )
    except ValuationInputError as exc:
        logger.error("valuation.snapshots_invalid reason=%s", exc)
        snapshots = {}

    accepted_ids = {ticker: snapshot.source_document_id for ticker, snapshot in snapshots.items()}
    freshness = check_official_freshness(
        POLICIES.values(),
        valuation_document_ids=accepted_ids,
        checked_at=checked_at,
        download_original=download_original,
        prior_results=prior_freshness,
    )
    # 第一遍检查下载并哈希官方原文后，使用标准化财务数据自动生成/刷新底稿。
    # 第二遍仅复查文件编号，不重复访问财务 provider 或改写底稿。
    refresh_failures: dict[str, str] = {}
    if download_original:
        snapshots, refresh_failures = refresh_snapshots(
            signals=signals,
            freshness=freshness,
            existing=snapshots,
            state_path=state_dir / "valuation_snapshots.json",
            checked_at=checked_at,
            reviewer=reviewer,
        )

    # 用同一次官方发现结果重新核验自动底稿。若 provider 短时失败而已有上一次
    # 可复算底稿，则继续显示最后已知值并标记 not_due；日志保留失败原因，避免
    # 因单一第三方短时故障让整列退回“待更新”。
    reconciled: dict[str, FreshnessResult] = {}
    for ticker, policy in POLICIES.items():
        snapshot = snapshots.get(ticker)
        result = freshness[ticker]
        if snapshot is None:
            reconciled[ticker] = result
            continue
        latest = result.latest_document
        if latest is not None and latest.document_id == snapshot.source_document_id:
            reconciled[ticker] = evaluate_freshness(
                policy=policy,
                latest_document=latest,
                valuation_document_id=snapshot.source_document_id,
                checked_at=checked_at,
            )
            continue
        reason = refresh_failures.get(ticker) or result.reason or "自动刷新暂不可用"
        retained_document = OfficialDocument(
            document_id=snapshot.source_document_id,
            document_type="LAST_KNOWN_GOOD",
            report_period=snapshot.financial_as_of,
            published_at=checked_at,
            discovered_at=checked_at,
            source_url=snapshot.source_url,
            source_domain="retained-official-source",
            title="上一份可复算官方底稿",
            content_hash=snapshot.source_content_hash,
        )
        reconciled[ticker] = FreshnessResult(
            ticker=ticker,
            status="not_due",
            checked_at=checked_at,
            latest_document=retained_document,
            valuation_document_id=snapshot.source_document_id,
            reason=f"沿用上一份可复算底稿：{reason}",
        )
        logger.warning("valuation.last_known_good ticker=%s reason=%s", ticker, reason)
    freshness = reconciled
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
    if qqqm_display is not None:
        displays["QQQM"] = qqqm_display
    displays = enforce_jump_guard(displays, state_dir=state_dir)
    current = sum(1 for value in displays.values() if not value.is_pending)
    for ticker, value in displays.items():
        logger.info(
            "valuation.display ticker=%s status=%s intrinsic=%s implied_return=%s source=%s",
            ticker,
            value.status,
            f"{value.intrinsic_value:.4f}" if value.intrinsic_value is not None else "none",
            f"{value.implied_return:.6f}" if value.implied_return is not None else "none",
            value.source_document_id or "none",
        )
    logger.info(
        "valuation.prepared current=%d pending=%d checked_at=%s",
        current,
        len(displays) - current,
        checked_at.isoformat(),
    )
    return displays, freshness
