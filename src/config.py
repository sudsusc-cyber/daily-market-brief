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
    logo_domain: str  # Clearbit / 公司主域名,用于拉 logo,如 'microsoft.com'

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
    Holding("KO", "The Coca-Cola Company", "coca-cola.com"),
    Holding("AXP", "American Express", "americanexpress.com"),
    Holding("0700.HK", "腾讯控股", "tencent.com"),
    Holding("9992.HK", "泡泡玛特", "popmart.com"),
]
