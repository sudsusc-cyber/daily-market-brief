"""14 只持仓的固定估值方法与官方来源注册表。

这里只放版本化、不允许 DeepSeek 每日改写的规则。经营预测与最新财务底稿
由独立 snapshot 保存；改公式必须提升 model_version。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from src.config import HOLDINGS

ValuationMethod = Literal["fcff", "fcfe", "residual_income", "sotp_multiple"]


@dataclass(frozen=True)
class ValuationPolicy:
    ticker: str
    formula_id: str
    model_version: str
    method: ValuationMethod
    hurdle_rate: float
    terminal_growth: float | None
    reporting_currency: str
    market_currency: str
    sec_cik: str | None = None
    hkex_stock_id: str | None = None
    official_domains: tuple[str, ...] = ()
    adr_ratio: float | None = None
    scenario: Literal["base", "weighted"] = "base"
    holding_years: int | None = None


def _p(
    ticker: str,
    formula_id: str,
    method: ValuationMethod,
    hurdle: float,
    growth: float | None,
    reporting_currency: str,
    market_currency: str,
    *,
    cik: str | None = None,
    domains: tuple[str, ...] = (),
    adr_ratio: float | None = None,
    hkex_stock_id: str | None = None,
    holding_years: int | None = None,
) -> ValuationPolicy:
    return ValuationPolicy(
        ticker=ticker,
        formula_id=formula_id,
        model_version="1.0",
        method=method,
        hurdle_rate=hurdle,
        terminal_growth=growth,
        reporting_currency=reporting_currency,
        market_currency=market_currency,
        sec_cik=cik,
        hkex_stock_id=hkex_stock_id,
        official_domains=domains,
        adr_ratio=adr_ratio,
        holding_years=holding_years,
    )


POLICIES: dict[str, ValuationPolicy] = {
    "MSFT": _p("MSFT", "msft_consolidated_fcff_v1", "fcff", 0.10, 0.030, "USD", "USD", cik="0000789019", domains=("microsoft.com", "sec.gov")),
    "COST": _p("COST", "cost_consolidated_fcff_v1", "fcff", 0.10, 0.025, "USD", "USD", cik="0000909832", domains=("investor.costco.com", "sec.gov")),
    "AAPL": _p("AAPL", "aapl_product_services_fcfe_v1", "fcfe", 0.10, 0.025, "USD", "USD", cik="0000320193", domains=("apple.com", "sec.gov")),
    "NVDA": _p("NVDA", "nvda_base_scenario_fcff_v1", "fcff", 0.10, 0.030, "USD", "USD", cik="0001045810", domains=("investor.nvidia.com", "sec.gov")),
    "TSM": _p("TSM", "tsm_midcycle_fcff_v1", "fcff", 0.11, 0.025, "TWD", "USD", cik="0001046179", domains=("investor.tsmc.com", "mops.twse.com.tw", "sec.gov"), adr_ratio=5.0),
    "MCO": _p("MCO", "mco_consolidated_fcff_v1", "fcff", 0.10, 0.025, "USD", "USD", cik="0001059556", domains=("ir.moodys.com", "sec.gov")),
    "GOOG": _p("GOOG", "goog_consolidated_fcff_investments_v1", "fcff", 0.10, 0.030, "USD", "USD", cik="0001652044", domains=("abc.xyz", "sec.gov")),
    "BRK.B": _p("BRK.B", "brkb_asset_operating_sotp_v1", "sotp_multiple", 0.10, None, "USD", "USD", cik="0001067983", domains=("berkshirehathaway.com", "sec.gov"), holding_years=5),
    "KO": _p("KO", "ko_organic_growth_fcfe_v1", "fcfe", 0.10, 0.025, "USD", "USD", cik="0000021344", domains=("investors.coca-colacompany.com", "sec.gov")),
    "AXP": _p("AXP", "axp_residual_income_v1", "residual_income", 0.10, 0.025, "USD", "USD", cik="0000004962", domains=("ir.americanexpress.com", "sec.gov")),
    "0700.HK": _p("0700.HK", "tencent_consolidated_fcff_investments_v1", "fcff", 0.11, 0.030, "CNY", "HKD", domains=("hkexnews.hk", "tencent.com"), hkex_stock_id="7609"),
    "9992.HK": _p("9992.HK", "popmart_base_scenario_fcff_v1", "fcff", 0.12, 0.030, "CNY", "HKD", domains=("hkexnews.hk", "prod-out-res.popmart.com", "popmart.com"), hkex_stock_id="1000068054"),
    "MA": _p("MA", "ma_net_revenue_fcfe_v1", "fcfe", 0.10, 0.030, "USD", "USD", cik="0001141391", domains=("investor.mastercard.com", "sec.gov")),
    "LIN": _p("LIN", "lin_consolidated_fcff_v1", "fcff", 0.10, 0.025, "USD", "USD", cik="0001707925", domains=("investors.linde.com", "sec.gov")),
}


def validate_policy_coverage() -> None:
    holdings = {holding.ticker for holding in HOLDINGS}
    policies = set(POLICIES)
    if holdings != policies:
        raise RuntimeError(
            f"估值政策与持仓不一致 missing={sorted(holdings - policies)} "
            f"extra={sorted(policies - holdings)}"
        )


validate_policy_coverage()
