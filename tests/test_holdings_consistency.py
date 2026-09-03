"""持仓清单一致性守卫。

把"新增持仓时漏配某处逐公司配置"变成测试失败,避免全局静默漂移:
  - 每只持仓必须在 company_news._RELEVANCE_KEYWORDS 里有相关性关键词
    (美股走 Finnhub,缺关键词会降级为不过滤,噪音很大)
  - 每只持仓必须在 news_summarizer._CN_NAME_HINT 里有中文名
    (「昨日动态」分行统一中文名,缺了会回退英文名,视觉不一致)
  - 持仓的 slug 必须有对应的 logo 文件(否则邮件该行无图标)

并显式断言 2026-06 新增的 MA / LIN 已落位,作为本次改动的回归锚点。
"""
from __future__ import annotations

from pathlib import Path

from src.collectors.company_news import _RELEVANCE_KEYWORDS
from src.config import COMPANY_HOLDINGS, HOLDINGS
from src.processors.news_summarizer import _CN_NAME_HINT
from src.valuation.instructions import FORMULA_INSTRUCTIONS
from src.valuation.policy import POLICIES

_LOGOS_DIR = Path(__file__).resolve().parents[1] / "assets" / "logos"


def test_mastercard_and_linde_present() -> None:
    tickers = {h.ticker for h in HOLDINGS}
    assert {"MA", "LIN"} <= tickers


def test_every_holding_has_relevance_keywords() -> None:
    """港股走 Google News(query 即公司名)不需要关键词;美股必须有。"""
    missing = [
        h.ticker
        for h in COMPANY_HOLDINGS
        if not h.ticker.endswith(".HK") and h.ticker not in _RELEVANCE_KEYWORDS
    ]
    assert not missing, f"美股持仓缺新闻相关性关键词: {missing}"


def test_every_holding_has_cn_name_hint() -> None:
    missing = [h.ticker for h in COMPANY_HOLDINGS if h.ticker not in _CN_NAME_HINT]
    assert not missing, f"持仓缺中文名映射: {missing}"


def test_every_holding_has_logo_file() -> None:
    missing = [
        h.ticker
        for h in HOLDINGS
        if not any((_LOGOS_DIR / f"{h.slug}.{ext}").exists() for ext in ("png", "jpg", "jpeg"))
    ]
    assert not missing, f"持仓缺 logo 文件: {missing}"


def test_every_holding_has_fixed_valuation_policy() -> None:
    assert set(POLICIES) == {holding.ticker for holding in COMPANY_HOLDINGS}
    assert set(FORMULA_INSTRUCTIONS) == set(POLICIES)


def test_qqqm_is_a_signal_holding_not_a_company_valuation():
    qqqm = next(h for h in HOLDINGS if h.ticker == "QQQM")
    assert qqqm.asset_type == "etf"
    assert qqqm not in COMPANY_HOLDINGS
    assert {h.ticker for h in HOLDINGS} - {h.ticker for h in COMPANY_HOLDINGS} == {"QQQM"}
    assert "QQQM" not in POLICIES
