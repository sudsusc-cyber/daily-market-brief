"""从最新标准化财务数据自动生成可复算的估值底稿。

官方文件的发现、编号和 SHA-256 仍由 ``freshness.py`` 负责；本模块只把
provider 已标准化的财务报表转换成逐股固定公式需要的输入。最终内在价值和 IRR
继续由 ``engine.py`` 计算，模型或数据源都不能直接写最终展示值。
"""

from __future__ import annotations

import json
import logging
import math
import os
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from typing import Any

import yfinance as yf

from src.collectors.stocks import StockSignal
from src.valuation.engine import ValuationInputError, ValuationSnapshot, snapshot_from_dict
from src.valuation.models import FreshnessResult, OfficialDocument
from src.valuation.policy import POLICIES, ValuationPolicy

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AutoRule:
    """不随每日运行漂移的公司级估值参数。"""

    growth_floor: float
    growth_cap: float
    forecast_years: int = 5
    terminal_rote: float | None = None
    retention_rate: float = 0.40
    sotp_book_multiple: float | None = None
    sotp_book_growth_cap: float | None = None


AUTO_RULES: dict[str, AutoRule] = {
    "MSFT": AutoRule(0.06, 0.15),
    "COST": AutoRule(0.04, 0.08),
    "AAPL": AutoRule(0.03, 0.07),
    "NVDA": AutoRule(0.08, 0.20),
    "TSM": AutoRule(0.06, 0.14),
    "MCO": AutoRule(0.04, 0.10),
    "GOOG": AutoRule(0.06, 0.14),
    "BRK.B": AutoRule(
        0.04,
        0.10,
        sotp_book_multiple=1.35,
        sotp_book_growth_cap=0.09,
    ),
    "KO": AutoRule(0.03, 0.07),
    "AXP": AutoRule(0.04, 0.10, terminal_rote=0.15, retention_rate=0.35),
    "0700.HK": AutoRule(0.05, 0.12),
    "9992.HK": AutoRule(0.07, 0.18),
    "MA": AutoRule(0.05, 0.12),
    "LIN": AutoRule(0.03, 0.08),
}


def validate_auto_rule_coverage() -> None:
    if set(AUTO_RULES) != set(POLICIES):
        raise RuntimeError("自动估值规则与固定政策覆盖不一致")


validate_auto_rule_coverage()


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _row_values(frame: Any, names: tuple[str, ...], *, limit: int = 4) -> list[float]:
    if frame is None or getattr(frame, "empty", True):
        return []
    for name in names:
        if name not in frame.index:
            continue
        values: list[float] = []
        for raw in frame.loc[name].dropna().iloc[:limit].tolist():
            value = _finite(raw)
            if value is not None:
                values.append(value)
        if values:
            return values
    return []


def _latest(frame: Any, names: tuple[str, ...]) -> float | None:
    values = _row_values(frame, names, limit=1)
    return values[0] if values else None


def _latest_period(*frames: Any, fallback: str | None) -> str:
    periods: list[str] = []
    for frame in frames:
        for column in getattr(frame, "columns", []):
            try:
                periods.append(column.date().isoformat())
            except AttributeError:
                text = str(column).strip()
                if text:
                    periods.append(text[:10])
            break
    return max(periods) if periods else (fallback or datetime.now(UTC).date().isoformat())


def _shares(ticker: str, income: Any, balance: Any) -> float:
    # TSM 的损益表股数是 ADS 等价数；经营数据以 TWD、普通股口径计算，必须使用
    # 资产负债表的约 259 亿普通股，再由 engine 按 1 ADS=5 股换算。
    if ticker == "TSM":
        value = _latest(balance, ("Ordinary Shares Number", "Share Issued"))
    else:
        value = _latest(income, ("Diluted Average Shares", "Basic Average Shares"))
        if value is None:
            value = _latest(balance, ("Ordinary Shares Number", "Share Issued"))
    if value is None or value <= 0:
        raise ValuationInputError("标准化财务数据缺有效稀释股数")
    return value


def _normalized_annual_value(annual: Any, quarterly: Any, names: tuple[str, ...]) -> float:
    annual_values = [value for value in _row_values(annual, names, limit=3) if value > 0]
    quarterly_values = _row_values(quarterly, names, limit=4)
    ttm = sum(quarterly_values) if len(quarterly_values) == 4 and all(v > 0 for v in quarterly_values) else None
    if ttm is not None and annual_values:
        # TTM 保留最新变化，三年中位数降低资本开支或营运资本单年尖峰的影响。
        return 0.60 * ttm + 0.40 * median(annual_values)
    if ttm is not None:
        return ttm
    if annual_values:
        return median(annual_values)
    raise ValuationInputError(f"标准化财务数据缺正值字段 {names[0]}")


def _historical_growth(values: list[float], *, floor: float, cap: float) -> float:
    positives = [value for value in values[:4] if value > 0]
    if len(positives) < 2:
        return floor
    newest, oldest = positives[0], positives[-1]
    years = len(positives) - 1
    try:
        cagr = (newest / oldest) ** (1 / years) - 1
    except (ArithmeticError, ValueError):
        return floor
    if not math.isfinite(cagr):
        return floor
    return min(max(cagr, floor), cap)


def _project_cash_flows(
    *,
    base_per_share: float,
    start_growth: float,
    terminal_growth: float,
    years: int,
) -> tuple[float, ...]:
    if base_per_share <= 0:
        raise ValuationInputError("每股标准化自由现金流不是正值")
    value = base_per_share
    projected: list[float] = []
    for year in range(years):
        weight = year / max(years - 1, 1)
        growth = start_growth + (terminal_growth - start_growth) * weight
        value *= 1 + growth
        projected.append(value)
    return tuple(projected)


def _quote(symbol: str, ticker_factory: Callable[[str], Any]) -> float:
    history = ticker_factory(symbol).history(period="5d", auto_adjust=False)
    closes = _row_values(history.T, ("Close",), limit=5) if not getattr(history, "empty", True) else []
    if not closes and not getattr(history, "empty", True) and "Close" in history:
        closes = [value for value in (_finite(v) for v in history["Close"].dropna().tolist()[::-1]) if value]
    if not closes or closes[0] <= 0:
        raise ValuationInputError(f"即期汇率 {symbol} 不可用")
    return closes[0]


def _fx_reporting_per_market(
    policy: ValuationPolicy,
    ticker_factory: Callable[[str], Any],
) -> float | None:
    if policy.reporting_currency == policy.market_currency:
        return None
    if policy.ticker == "TSM":
        return _quote("TWD=X", ticker_factory)  # TWD / USD
    if policy.market_currency == "HKD" and policy.reporting_currency == "CNY":
        return _quote("HKDCNY=X", ticker_factory)  # CNY / HKD
    raise ValuationInputError("未注册报告币到交易币的即期汇率")


def _capital_inputs(balance: Any, income: Any, *, shares: float, current_price: float) -> dict[str, float]:
    cash = _latest(
        balance,
        (
            "Cash Cash Equivalents And Short Term Investments",
            "Cash And Cash Equivalents",
            "Cash Financial",
        ),
    ) or 0.0
    debt = _latest(balance, ("Total Debt",)) or 0.0
    equity_market_value = max(current_price * shares, 0.0)
    capital = equity_market_value + max(debt, 0.0)
    debt_weight = max(debt, 0.0) / capital if capital > 0 else 0.0
    tax = _latest(income, ("Tax Provision",))
    pretax = _latest(income, ("Pretax Income",))
    tax_rate = tax / pretax if tax is not None and pretax and pretax > 0 else 0.21
    tax_rate = min(max(tax_rate, 0.0), 0.35)
    return {
        "net_debt_per_share": (debt - cash) / shares,
        "equity_weight": 1.0 - debt_weight,
        "debt_weight": debt_weight,
        "debt_cost": 0.045,
        "tax_rate": tax_rate,
    }


def _base_payload(
    *,
    policy: ValuationPolicy,
    document: OfficialDocument,
    checked_at: datetime,
    financial_as_of: str,
) -> dict[str, Any]:
    if not document.content_hash:
        raise ValuationInputError("最新官方原文缺 SHA-256")
    return {
        "ticker": policy.ticker,
        "formula_id": policy.formula_id,
        "model_version": policy.model_version,
        "method": policy.method,
        "source_document_id": document.document_id,
        "source_url": document.source_url,
        "source_content_hash": document.content_hash,
        "financial_as_of": financial_as_of,
        "approved_at": checked_at.date().isoformat(),
        "data_provider": "Yahoo Finance standardized financial statements",
        "data_retrieved_at": checked_at.astimezone(UTC).isoformat(),
        "normalization_version": "automatic-normalized-financials-v1",
        "currency_symbol": {"USD": "$", "HKD": "HK$", "CNY": "¥", "TWD": "NT$"}.get(
            policy.market_currency,
            policy.market_currency,
        ),
        "scenario": policy.scenario,
        "discount_rate": policy.hurdle_rate,
        "terminal_growth": policy.terminal_growth,
        "adr_ratio": policy.adr_ratio,
    }


def build_snapshot(
    *,
    signal: StockSignal,
    freshness: FreshnessResult,
    checked_at: datetime,
    ticker_factory: Callable[[str], Any] = yf.Ticker,
) -> ValuationSnapshot:
    policy = POLICIES[signal.holding.ticker]
    rule = AUTO_RULES[policy.ticker]
    document = freshness.latest_document
    if document is None:
        raise ValuationInputError("官方源没有可绑定的最新财务文件")
    if signal.last_close is None or signal.last_close <= 0:
        raise ValuationInputError("现价不可用，无法完成资本结构或 IRR 输入")

    ticker = ticker_factory(signal.holding.yfinance_symbol)
    cashflow = ticker.cashflow
    quarterly_cashflow = ticker.quarterly_cashflow
    income = ticker.financials
    quarterly_income = ticker.quarterly_financials
    balance = ticker.balance_sheet
    shares = _shares(policy.ticker, income, balance)
    financial_as_of = _latest_period(cashflow, income, balance, fallback=document.report_period)
    raw = _base_payload(
        policy=policy,
        document=document,
        checked_at=checked_at,
        financial_as_of=financial_as_of,
    )
    raw["fx_reporting_per_market"] = _fx_reporting_per_market(policy, ticker_factory)

    if policy.method in {"fcff", "fcfe"}:
        normalized_fcf = _normalized_annual_value(
            cashflow,
            quarterly_cashflow,
            ("Free Cash Flow",),
        )
        history = _row_values(cashflow, ("Free Cash Flow",), limit=4)
        start_growth = _historical_growth(
            history,
            floor=rule.growth_floor,
            cap=rule.growth_cap,
        )
        if policy.terminal_growth is None:
            raise ValuationInputError("现金流方法缺固定永续增长率")
        raw["cash_flows_per_share"] = list(
            _project_cash_flows(
                base_per_share=normalized_fcf / shares,
                start_growth=start_growth,
                terminal_growth=policy.terminal_growth,
                years=rule.forecast_years,
            )
        )
        raw.update(
            _capital_inputs(
                balance,
                quarterly_income if not getattr(quarterly_income, "empty", True) else income,
                shares=shares,
                current_price=signal.last_close,
            )
        )
        # FCFE 已经是股东现金流，不再做净债务桥接。
        if policy.method == "fcfe":
            raw["net_debt_per_share"] = 0.0
            raw["equity_weight"] = 1.0
            raw["debt_weight"] = 0.0

    elif policy.method == "residual_income":
        equity = _latest(balance, ("Stockholders Equity", "Common Stock Equity"))
        if equity is None or equity <= 0:
            raise ValuationInputError("剩余收益模型缺有效普通股股东权益")
        normalized_income = _normalized_annual_value(
            income,
            quarterly_income,
            ("Net Income", "Net Income Common Stockholders"),
        )
        current_rote = min(max(normalized_income / equity, 0.08), 0.35)
        terminal_rote = rule.terminal_rote
        if terminal_rote is None:
            raise ValuationInputError("剩余收益规则缺固定终值 ROTE")
        book = equity / shares
        books = [book]
        rotes: list[float] = []
        for year in range(rule.forecast_years):
            weight = year / max(rule.forecast_years - 1, 1)
            rote = current_rote + (terminal_rote - current_rote) * weight
            rotes.append(rote)
            books.append(books[-1] * (1 + rote * rule.retention_rate))
        raw.update(
            {
                "book_values_per_share": books,
                "rote_path": rotes,
                "terminal_rote": terminal_rote,
            }
        )

    elif policy.method == "sotp_multiple":
        equity = _latest(balance, ("Stockholders Equity", "Common Stock Equity"))
        if equity is None or equity <= 0:
            raise ValuationInputError("SOTP 自动底稿缺有效股东权益")
        if rule.sotp_book_multiple is None or rule.sotp_book_growth_cap is None:
            raise ValuationInputError("SOTP 自动规则缺固定倍数或增长上限")
        book_history = _row_values(balance, ("Stockholders Equity",), limit=4)
        book_growth = _historical_growth(
            book_history,
            floor=rule.growth_floor,
            cap=rule.sotp_book_growth_cap,
        )
        current_value = equity / shares * rule.sotp_book_multiple
        years = policy.holding_years or rule.forecast_years
        raw.update(
            {
                "approved_intrinsic_value": current_value,
                "sotp_exit_value_per_share": current_value * ((1 + book_growth) ** years),
                "sotp_distributions_per_share": [0.0] * years,
            }
        )
    else:  # pragma: no cover - ValuationMethod 已穷举
        raise ValuationInputError(f"不支持的自动估值方法 {policy.method}")

    return snapshot_from_dict(raw)


def _snapshot_payload(snapshot: ValuationSnapshot) -> dict[str, Any]:
    payload = asdict(snapshot)
    for key, value in list(payload.items()):
        if isinstance(value, tuple):
            payload[key] = list(value)
    return payload


def save_snapshots(snapshots: dict[str, ValuationSnapshot], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "schema_version": "1.0",
        "generated_by": "automatic-normalized-financials-v1",
        "snapshots": [_snapshot_payload(snapshots[ticker]) for ticker in POLICIES if ticker in snapshots],
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def refresh_snapshots(
    *,
    signals: list[StockSignal],
    freshness: dict[str, FreshnessResult],
    existing: dict[str, ValuationSnapshot],
    state_path: Path,
    checked_at: datetime,
    ticker_factory: Callable[[str], Any] = yf.Ticker,
    reviewer: Any | None = None,
) -> tuple[dict[str, ValuationSnapshot], dict[str, str]]:
    """逐只刷新；单只失败时保留上一次可复算底稿，不拖垮整封邮件。"""
    snapshots = dict(existing)
    failures: dict[str, str] = {}
    refreshed = 0
    for signal in signals:
        ticker = signal.holding.ticker
        result = freshness.get(ticker)
        if result is None:
            failures[ticker] = "缺官方新鲜度结果"
            continue
        try:
            policy = POLICIES[ticker]
            document = result.latest_document
            previous = existing.get(ticker)
            if (
                previous is not None
                and document is not None
                and previous.source_document_id == document.document_id
                and previous.formula_id == policy.formula_id
                and previous.model_version == policy.model_version
            ):
                fx = _fx_reporting_per_market(policy, ticker_factory)
                snapshots[ticker] = replace(
                    previous,
                    fx_reporting_per_market=fx,
                    data_retrieved_at=checked_at.astimezone(UTC).isoformat(),
                )
                refreshed += 1
                continue

            candidate = build_snapshot(
                signal=signal,
                freshness=result,
                checked_at=checked_at,
                ticker_factory=ticker_factory,
            )
            if reviewer is not None and document is not None:
                try:
                    from src.valuation.official_review import review_snapshot

                    candidate = review_snapshot(
                        baseline=candidate,
                        policy=policy,
                        document=document,
                        client=reviewer,
                    )
                except Exception as exc:  # noqa: BLE001 - 确定性底稿仍可发布
                    logger.warning(
                        "valuation.official_review_fallback ticker=%s reason=%s: %s",
                        ticker,
                        type(exc).__name__,
                        str(exc)[:160],
                    )
            snapshots[ticker] = candidate
            refreshed += 1
        except Exception as exc:  # noqa: BLE001 - 单股安全降级
            message = f"{type(exc).__name__}: {str(exc)[:160]}"
            failures[ticker] = message
            logger.warning("valuation.auto_snapshot_failed ticker=%s reason=%s", ticker, message)
    if snapshots:
        save_snapshots(snapshots, state_path)
    logger.info(
        "valuation.auto_snapshots refreshed=%d retained=%d failed=%d",
        refreshed,
        sum(1 for ticker in failures if ticker in existing),
        len(failures),
    )
    return snapshots, failures
