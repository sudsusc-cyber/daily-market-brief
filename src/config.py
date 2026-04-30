"""
项目配置(非 secret,可入库)。

- HOLDINGS:权威持仓清单,与 PLAN.md 附录 A 一致
- ticker 用人类可读的标准记号(BRK.B / 0700.HK 等)
- 各 collector 内部如需特殊符号(如 yfinance 把 BRK.B 写作 BRK-B),
  通过 Holding 上的属性方法转换
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Holding:
    """单只持仓股票的元信息"""

    ticker: str  # 标准记号,邮件展示与日志用
    name: str  # 中文/英文展示名

    @property
    def yfinance_symbol(self) -> str:
        """yfinance 兼容符号:美股的 . 替换为 -,港股保持原样"""
        if self.ticker.endswith(".HK"):
            return self.ticker
        return self.ticker.replace(".", "-")


HOLDINGS: list[Holding] = [
    Holding("MSFT", "Microsoft"),
    Holding("COST", "Costco Wholesale"),
    Holding("AAPL", "Apple"),
    Holding("NVDA", "NVIDIA"),
    Holding("TSM", "Taiwan Semiconductor (ADR)"),
    Holding("MCO", "Moody's"),
    Holding("GOOG", "Alphabet C"),
    Holding("BRK.B", "Berkshire Hathaway B"),
    Holding("KO", "The Coca-Cola Company"),
    Holding("AXP", "American Express"),
    Holding("0700.HK", "腾讯控股"),
    Holding("9992.HK", "泡泡玛特"),
]
