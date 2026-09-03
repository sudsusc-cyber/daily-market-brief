"""
项目配置(非 secret,可入库)。

- HOLDINGS:权威持仓清单,与 PLAN.md 附录 A 一致
- ticker 用人类可读的标准记号(BRK.B / 0700.HK 等)
- 各 collector 内部如需特殊符号(如 yfinance 把 BRK.B 写作 BRK-B),
  通过 Holding 上的属性方法转换
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

BuyLine = Literal["120w", "200w", "250d"]


@dataclass(frozen=True)
class BuyStrategy:
    key: str
    label: str
    tickers: tuple[str, ...]
    dca_line: BuyLine | None
    lump_line: BuyLine

    @property
    def line_labels(self) -> tuple[str, ...]:
        labels = {"120w": "120 周", "200w": "200 周", "250d": "250 日"}
        lines = (self.dca_line, self.lump_line) if self.dca_line else (self.lump_line,)
        return tuple(labels[line] for line in lines)

    @property
    def reference_line_labels(self) -> tuple[str, ...]:
        """展示用双参考线；不改变 line_labels 所描述的实际触发条件。"""
        return self.line_labels if self.dca_line else ("120 周", *self.line_labels)


# 用户九五策略；顺序同时决定邮件分组顺序。港股规则相同但独立展示。
BUY_STRATEGIES = (
    BuyStrategy("us_weekly", "美股 · 双线", ("MSFT", "GOOG", "AAPL", "BRK.B", "QQQM"), "120w", "200w"),
    BuyStrategy("us_daily_weekly", "美股 · 日周线", ("NVDA", "TSM"), "250d", "120w"),
    BuyStrategy("us_deep", "美股 · 单线", ("COST", "MA", "MCO", "KO", "LIN", "AXP"), None, "200w"),
    BuyStrategy("hk_weekly", "港股 · 双线", ("0700.HK", "9992.HK"), "120w", "200w"),
)


def buy_strategy(ticker: str) -> BuyStrategy:
    # GOOGL 不加入持仓；若明确传入，按同组规则使用它自身行情。
    key = {"GOOGL": "GOOG", "BRK-B": "BRK.B"}.get(ticker, ticker)
    for strategy in BUY_STRATEGIES:
        if key in strategy.tickers:
            return strategy
    raise ValueError(f"未配置买入策略: {ticker}")


@dataclass(frozen=True)
class Holding:
    """单只持仓股票的元信息"""

    ticker: str  # 标准记号,邮件展示与日志用
    name: str  # 中文/英文展示名
    logo_domain: str  # Clearbit / 公司主域名,用于拉 logo,如 'microsoft.com'
    asset_type: Literal["stock", "etf"] = "stock"

    @property
    def yfinance_symbol(self) -> str:
        """yfinance 兼容符号:美股的 . 替换为 -,港股保持原样"""
        if self.ticker.endswith(".HK"):
            return self.ticker
        return self.ticker.replace(".", "-")

    @property
    def slug(self) -> str:
        """文件名 / CID 安全标识符,如 BRK.B -> BRK_B,0700.HK -> 0700_HK"""
        return self.ticker.replace(".", "_").replace("-", "_")

    @property
    def logo_cid(self) -> str:
        """邮件中 <img src='cid:...'> 的 Content-ID"""
        return f"logo_{self.slug}"


HOLDINGS: list[Holding] = [
    Holding("MSFT", "Microsoft", "microsoft.com"),
    Holding("COST", "Costco Wholesale", "costco.com"),
    Holding("AAPL", "Apple", "apple.com"),
    Holding("NVDA", "NVIDIA", "nvidia.com"),
    Holding("TSM", "台積電", "tsmc.com"),
    Holding("MCO", "Moody's", "moodys.com"),
    # GOOG 是 Alphabet C 类股,但 Alphabet 主域 abc.xyz 的 favicon 太低分;
    # 用 google.com favicon 视觉更清晰,且对用户更易识别
    Holding("GOOG", "Alphabet C", "google.com"),
    Holding("BRK.B", "Berkshire Hathaway B", "berkshirehathaway.com"),
    Holding("KO", "Coca-Cola", "coca-cola.com"),
    Holding("AXP", "American Express", "americanexpress.com"),
    Holding("0700.HK", "腾讯控股", "tencent.com"),
    Holding("9992.HK", "泡泡玛特", "popmart.com"),
    # 2026-06 新增:万事达 / 林德气体(均为美股,走 Finnhub + yfinance 通用路径)。
    # 追加在末尾——多个测试用 HOLDINGS[idx] 索引引用既有持仓,插入中间会破坏索引。
    Holding("MA", "Mastercard", "mastercard.com"),
    Holding("LIN", "Linde", "linde.com"),
    # QQQM 接入持仓行情/买入信号；估值由独立 v1.5 ETF 模块处理，不套用上市公司模型。
    Holding("QQQM", "Invesco Nasdaq 100", "invesco.com", asset_type="etf"),
]

COMPANY_HOLDINGS: list[Holding] = [holding for holding in HOLDINGS if holding.asset_type == "stock"]
