"""确定性估值与隐含收益率求解器。

DeepSeek 只能生成符合 schema 的输入；这里重新计算所有最终值。任何缺失、非有限数、
无唯一根或官方文件不新鲜，都会返回“待更新”，不会用安全边际反推近似收益率。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.valuation.models import FreshnessResult, ValuationDisplay
from src.valuation.policy import ValuationPolicy


class ValuationInputError(ValueError):
    """估值底稿结构或经济含义不满足 v1 规则。"""


@dataclass(frozen=True)
class ValuationSnapshot:
    ticker: str
    formula_id: str
    model_version: str
    method: str
    source_document_id: str
    source_url: str
    source_content_hash: str | None
    financial_as_of: str
    approved_at: str
    currency_symbol: str
    data_provider: str | None = None
    data_retrieved_at: str | None = None
    normalization_version: str | None = None
    scenario: str = "base"
    fx_reporting_per_market: float | None = None
    adr_ratio: float | None = None
    discount_rate: float | None = None
    terminal_growth: float | None = None
    cash_flows_per_share: tuple[float, ...] = ()
    net_debt_per_share: float = 0.0
    non_operating_assets_per_share: float = 0.0
    equity_weight: float = 1.0
    debt_weight: float = 0.0
    debt_cost: float = 0.0
    tax_rate: float = 0.0
    book_values_per_share: tuple[float, ...] = ()
    rote_path: tuple[float, ...] = ()
    terminal_rote: float | None = None
    approved_intrinsic_value: float | None = None
    sotp_exit_value_per_share: float | None = None
    sotp_distributions_per_share: tuple[float, ...] = ()


def _finite(value: Any, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValuationInputError(f"{field} 不是数值") from exc
    if not math.isfinite(number):
        raise ValuationInputError(f"{field} 不是有限数")
    return number


def snapshot_from_dict(raw: dict[str, Any]) -> ValuationSnapshot:
    required = (
        "ticker",
        "formula_id",
        "model_version",
        "method",
        "source_document_id",
        "source_url",
        "financial_as_of",
        "approved_at",
        "currency_symbol",
    )
    missing = [key for key in required if not str(raw.get(key, "")).strip()]
    if missing:
        raise ValuationInputError(f"估值底稿缺字段: {missing}")

    def floats(key: str) -> tuple[float, ...]:
        values = raw.get(key) or []
        if not isinstance(values, list):
            raise ValuationInputError(f"{key} 必须是数组")
        return tuple(_finite(value, f"{key}[{index}]") for index, value in enumerate(values))

    def optional(key: str) -> float | None:
        return None if raw.get(key) is None else _finite(raw[key], key)

    return ValuationSnapshot(
        ticker=str(raw["ticker"]).strip(),
        formula_id=str(raw["formula_id"]).strip(),
        model_version=str(raw["model_version"]).strip(),
        method=str(raw["method"]).strip(),
        source_document_id=str(raw["source_document_id"]).strip(),
        source_url=str(raw["source_url"]).strip(),
        source_content_hash=(
            str(raw["source_content_hash"]).strip()
            if raw.get("source_content_hash")
            else None
        ),
        financial_as_of=str(raw["financial_as_of"]).strip(),
        approved_at=str(raw["approved_at"]).strip(),
        currency_symbol=str(raw["currency_symbol"]).strip(),
        data_provider=(str(raw["data_provider"]).strip() if raw.get("data_provider") else None),
        data_retrieved_at=(
            str(raw["data_retrieved_at"]).strip() if raw.get("data_retrieved_at") else None
        ),
        normalization_version=(
            str(raw["normalization_version"]).strip()
            if raw.get("normalization_version")
            else None
        ),
        scenario=str(raw.get("scenario", "base")).strip(),
        fx_reporting_per_market=optional("fx_reporting_per_market"),
        adr_ratio=optional("adr_ratio"),
        discount_rate=optional("discount_rate"),
        terminal_growth=optional("terminal_growth"),
        cash_flows_per_share=floats("cash_flows_per_share"),
        net_debt_per_share=_finite(raw.get("net_debt_per_share", 0), "net_debt_per_share"),
        non_operating_assets_per_share=_finite(
            raw.get("non_operating_assets_per_share", 0),
            "non_operating_assets_per_share",
        ),
        equity_weight=_finite(raw.get("equity_weight", 1), "equity_weight"),
        debt_weight=_finite(raw.get("debt_weight", 0), "debt_weight"),
        debt_cost=_finite(raw.get("debt_cost", 0), "debt_cost"),
        tax_rate=_finite(raw.get("tax_rate", 0), "tax_rate"),
        book_values_per_share=floats("book_values_per_share"),
        rote_path=floats("rote_path"),
        terminal_rote=optional("terminal_rote"),
        approved_intrinsic_value=optional("approved_intrinsic_value"),
        sotp_exit_value_per_share=optional("sotp_exit_value_per_share"),
        sotp_distributions_per_share=floats("sotp_distributions_per_share"),
    )


def load_snapshots(*paths: Path) -> dict[str, ValuationSnapshot]:
    """优先读取靠前路径；同一 ticker 后出现的文件不会覆盖已加载底稿。"""
    snapshots: dict[str, ValuationSnapshot] = {}
    for path in paths:
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValuationInputError(f"无法读取估值底稿 {path}: {exc}") from exc
        rows = payload.get("snapshots") if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            raise ValuationInputError(f"{path} 顶层必须是 snapshots 数组")
        for row in rows:
            if not isinstance(row, dict):
                raise ValuationInputError(f"{path} 含非对象底稿")
            snapshot = snapshot_from_dict(row)
            snapshots.setdefault(snapshot.ticker, snapshot)
    return snapshots


def present_value_with_terminal(
    cash_flows: tuple[float, ...],
    *,
    rate: float,
    terminal_growth: float,
) -> float:
    if not cash_flows:
        raise ValuationInputError("现金流预测为空")
    if rate <= terminal_growth:
        raise ValuationInputError("折现率必须高于永续增长率")
    if rate <= -1:
        raise ValuationInputError("折现率无效")
    pv = sum(flow / ((1 + rate) ** year) for year, flow in enumerate(cash_flows, 1))
    terminal_cash_flow = cash_flows[-1] * (1 + terminal_growth)
    terminal_value = terminal_cash_flow / (rate - terminal_growth)
    return pv + terminal_value / ((1 + rate) ** len(cash_flows))


def solve_implied_rate(
    *,
    target_value: float,
    value_at_rate,
    terminal_growth: float,
    upper: float = 0.50,
    tolerance: float = 1e-7,
) -> float | None:
    """Brent 依赖不引入项目；单调 DCF 用带保护的二分求唯一根。"""
    if target_value <= 0:
        return None
    low = max(terminal_growth + 0.005, 0.001)
    high = upper
    if low >= high:
        return None

    def residual(rate: float) -> float:
        return float(value_at_rate(rate)) - target_value

    try:
        left = residual(low)
        right = residual(high)
    except (ArithmeticError, OverflowError, ValuationInputError, ValueError):
        return None
    if not (math.isfinite(left) and math.isfinite(right)) or left * right > 0:
        return None
    for _ in range(100):
        mid = (low + high) / 2
        value = residual(mid)
        if not math.isfinite(value):
            return None
        if abs(value) <= tolerance or high - low <= tolerance:
            return mid
        if left * value <= 0:
            high = mid
            right = value
        else:
            low = mid
            left = value
    return (low + high) / 2


def solve_holding_period_irr(
    *,
    purchase_price: float,
    exit_value: float,
    distributions: tuple[float, ...],
    years: int,
) -> float | None:
    """由固定持有期终值和期间分配反推年化 IRR。"""
    if purchase_price <= 0 or exit_value <= 0 or years <= 0:
        return None
    if len(distributions) > years:
        return None
    if any(not math.isfinite(value) or value < 0 for value in distributions):
        return None

    def residual(rate: float) -> float:
        if rate <= -1:
            return math.nan
        return (
            sum(value / ((1 + rate) ** (index + 1)) for index, value in enumerate(distributions))
            + exit_value / ((1 + rate) ** years)
            - purchase_price
        )

    low = -0.99
    high = 1.0
    left = residual(low)
    right = residual(high)
    while math.isfinite(right) and right > 0 and high < 100:
        high *= 2
        right = residual(high)
    if not math.isfinite(left) or not math.isfinite(right) or left * right > 0:
        return None
    for _ in range(120):
        mid = (low + high) / 2
        value = residual(mid)
        if not math.isfinite(value):
            return None
        if abs(value) <= 1e-7 or high - low <= 1e-7:
            return mid
        if left * value <= 0:
            high = mid
            right = value
        else:
            low = mid
            left = value
    return (low + high) / 2


def _validate_snapshot(snapshot: ValuationSnapshot, policy: ValuationPolicy) -> None:
    if snapshot.ticker != policy.ticker:
        raise ValuationInputError("ticker 与政策不一致")
    if snapshot.formula_id != policy.formula_id or snapshot.model_version != policy.model_version:
        raise ValuationInputError("公式 ID 或模型版本未经批准")
    if snapshot.method != policy.method:
        raise ValuationInputError("估值方法与固定政策不一致")
    if snapshot.scenario != policy.scenario:
        raise ValuationInputError("估值情景与固定政策不一致")
    if policy.method in {"fcff", "fcfe"} and (
        snapshot.discount_rate is None
        or abs(snapshot.discount_rate - policy.hurdle_rate) > 1e-12
    ):
        raise ValuationInputError("底稿折现率越权修改")
    if (
        snapshot.terminal_growth is not None
        and policy.terminal_growth is not None
        and abs(snapshot.terminal_growth - policy.terminal_growth) > 1e-12
    ):
        raise ValuationInputError("底稿永续增长率越权修改")


def _to_market_value(
    value: float,
    *,
    snapshot: ValuationSnapshot,
    policy: ValuationPolicy,
) -> float:
    """报告币每普通股换算为邮件所用市场币每交易单位。"""
    if policy.reporting_currency == policy.market_currency:
        return value
    fx = snapshot.fx_reporting_per_market
    if fx is None or fx <= 0:
        raise ValuationInputError("跨币种估值缺即期汇率")
    expected_ratio = policy.adr_ratio or 1.0
    actual_ratio = snapshot.adr_ratio or 1.0
    if abs(actual_ratio - expected_ratio) > 1e-12:
        raise ValuationInputError("ADR 换股比例与固定政策不一致")
    return value * expected_ratio / fx


def _validate_source_hash(
    snapshot: ValuationSnapshot,
    freshness: FreshnessResult,
) -> None:
    document = freshness.latest_document
    if document is None or document.content_hash is None:
        raise ValuationInputError("最新官方原文尚未完成哈希核验")
    if not snapshot.source_content_hash:
        raise ValuationInputError("批准底稿缺官方原文哈希")
    if snapshot.source_content_hash != document.content_hash:
        raise ValuationInputError("官方文件内容哈希与批准底稿不一致")


def _residual_income_value(snapshot: ValuationSnapshot, rate: float) -> float:
    books = snapshot.book_values_per_share
    rotes = snapshot.rote_path
    growth = snapshot.terminal_growth
    if growth is None or snapshot.terminal_rote is None:
        raise ValuationInputError("剩余收益底稿缺终值参数")
    if len(books) != len(rotes) + 1 or not rotes:
        raise ValuationInputError("剩余收益底稿的账面价值与 ROTE 期数不匹配")
    if rate <= growth:
        raise ValuationInputError("股权成本必须高于永续增长率")
    value = books[0]
    for year, rote in enumerate(rotes, 1):
        residual = (rote - rate) * books[year - 1]
        value += residual / ((1 + rate) ** year)
    terminal_residual = (snapshot.terminal_rote - rate) * books[-1] * (1 + growth)
    value += terminal_residual / (rate - growth) / ((1 + rate) ** len(rotes))
    return value


def calculate_display(
    *,
    policy: ValuationPolicy,
    snapshot: ValuationSnapshot,
    freshness: FreshnessResult,
    current_price: float | None,
) -> ValuationDisplay:
    base = dict(
        ticker=policy.ticker,
        hurdle_rate=policy.hurdle_rate,
        currency_symbol=snapshot.currency_symbol,
        financial_as_of=snapshot.financial_as_of,
        approved_at=snapshot.approved_at,
        source_url=snapshot.source_url,
        source_document_id=snapshot.source_document_id,
        formula_id=snapshot.formula_id,
        model_version=snapshot.model_version,
        return_label=("5Y SOTP IRR" if policy.method == "sotp_multiple" else "IRR"),
    )
    try:
        _validate_snapshot(snapshot, policy)
        if not freshness.may_publish_value:
            return ValuationDisplay(
                **base,
                status=freshness.status,
                warnings=(freshness.reason or "官方数据待核验",),
            )
        _validate_source_hash(snapshot, freshness)
        if current_price is None or current_price <= 0:
            return ValuationDisplay(**base, status="manual_review", warnings=("现价不可用",))

        if policy.method == "sotp_multiple":
            value = snapshot.approved_intrinsic_value
            if value is None or value <= 0:
                raise ValuationInputError("倍数加总底稿缺批准内在价值")
            if policy.holding_years is None:
                raise ValuationInputError("SOTP 缺固定持有期")
            if snapshot.sotp_exit_value_per_share is None:
                raise ValuationInputError("SOTP 缺五年退出价值")
            implied = solve_holding_period_irr(
                purchase_price=current_price,
                exit_value=snapshot.sotp_exit_value_per_share,
                distributions=snapshot.sotp_distributions_per_share,
                years=policy.holding_years,
            )
            if implied is None:
                raise ValuationInputError("SOTP 五年 IRR 无有效解")
            return ValuationDisplay(
                **base,
                status=freshness.status,
                intrinsic_value=value,
                implied_return=implied,
            )

        growth = snapshot.terminal_growth
        if growth is None:
            raise ValuationInputError("缺永续增长率")

        if policy.method == "fcfe":
            if snapshot.discount_rate is None:
                raise ValuationInputError("FCFE 缺冻结股权成本")
            value_fn = lambda rate: _to_market_value(  # noqa: E731
                present_value_with_terminal(
                    snapshot.cash_flows_per_share,
                    rate=rate,
                    terminal_growth=growth,
                ),
                snapshot=snapshot,
                policy=policy,
            )
            intrinsic = value_fn(snapshot.discount_rate)
            implied = solve_implied_rate(
                target_value=current_price,
                value_at_rate=value_fn,
                terminal_growth=growth,
            )
        elif policy.method == "fcff":
            if snapshot.discount_rate is None:
                raise ValuationInputError("FCFF 缺冻结 WACC")

            def equity_value_at_wacc(rate: float) -> float:
                enterprise = present_value_with_terminal(
                    snapshot.cash_flows_per_share,
                    rate=rate,
                    terminal_growth=growth,
                )
                reporting_value = (
                    enterprise
                    - snapshot.net_debt_per_share
                    + snapshot.non_operating_assets_per_share
                )
                return _to_market_value(
                    reporting_value,
                    snapshot=snapshot,
                    policy=policy,
                )

            intrinsic = equity_value_at_wacc(snapshot.discount_rate)
            implied_wacc = solve_implied_rate(
                target_value=current_price,
                value_at_rate=equity_value_at_wacc,
                terminal_growth=growth,
            )
            if implied_wacc is None or snapshot.equity_weight <= 0:
                implied = None
            else:
                implied = (
                    implied_wacc
                    - snapshot.debt_weight * snapshot.debt_cost * (1 - snapshot.tax_rate)
                ) / snapshot.equity_weight
                if not 0 < implied <= 0.50:
                    implied = None
        elif policy.method == "residual_income":
            intrinsic = _to_market_value(
                _residual_income_value(snapshot, policy.hurdle_rate),
                snapshot=snapshot,
                policy=policy,
            )
            implied = solve_implied_rate(
                target_value=current_price,
                value_at_rate=lambda rate: _to_market_value(
                    _residual_income_value(snapshot, rate),
                    snapshot=snapshot,
                    policy=policy,
                ),
                terminal_growth=growth,
            )
        else:
            raise ValuationInputError(f"未知估值方法 {policy.method}")

        if intrinsic <= 0 or not math.isfinite(intrinsic):
            raise ValuationInputError("计算出的内在价值无效")
        warnings: tuple[str, ...] = ()
        if implied is None:
            warnings = ("隐含收益率无唯一有效解",)
        return ValuationDisplay(
            **base,
            status=freshness.status,
            intrinsic_value=intrinsic,
            implied_return=implied,
            warnings=warnings,
        )
    except (ArithmeticError, OverflowError, ValuationInputError) as exc:
        return ValuationDisplay(
            **base,
            status="manual_review",
            warnings=(str(exc),),
        )
