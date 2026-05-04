"""
持仓股票信号采集:拉周线 → 算 120w/200w SMA → 判断 DCA / LUMP-SUM。

数据源:yfinance,周频,5 年历史。
失败处理:不抛异常,在 StockSignal.error 字段记录,让上层渲染时区分展示。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

import yfinance as yf

from src.config import Holding
from src.utils.retry import retry

logger = logging.getLogger(__name__)


SignalKind = Literal["DCA", "LUMP_SUM", "NONE"]


@dataclass
class StockSignal:
    """单只股票的信号结果。失败时 error 非空,其他价格字段为 None。"""

    holding: Holding
    last_close: float | None
    sma_120: float | None
    sma_200: float | None
    delta_120: float | None  # (last_close - sma_120) / sma_120
    delta_200: float | None
    signal: SignalKind
    error: str | None = None


def _judge_signal(last_close: float, sma_120: float, sma_200: float) -> SignalKind:
    """信号判断:200w 优先于 120w(更深的折扣)"""
    if last_close <= sma_200:
        return "LUMP_SUM"
    if last_close <= sma_120:
        return "DCA"
    return "NONE"


@retry(max_attempts=3, base_delay=2.0, backoff=2.5)
def _yf_history(symbol: str):
    """yfinance 周线拉取,带重试退避(限流时 2s/5s/12s 三次重试)。"""
    return yf.Ticker(symbol).history(period="5y", interval="1wk", auto_adjust=False)


def fetch_one(holding: Holding) -> StockSignal:
    """
    拉单只股票的周线并计算信号。

    任何异常都会被吞掉并写入 error,保证上层批处理不会因单只失败中断。
    周线不足 200 周(新股)按"数据不足"处理,error 字段说明原因。
    """
    symbol = holding.yfinance_symbol
    try:
        hist = _yf_history(symbol)
    except Exception as exc:  # noqa: BLE001
        logger.exception("yfinance.fetch_failed ticker=%s symbol=%s", holding.ticker, symbol)
        return _failed(holding, f"yfinance 异常: {type(exc).__name__}: {exc}")

    if hist is None or hist.empty:
        return _failed(holding, "yfinance 返回空数据(ticker 可能错误或临时不可达)")

    rows = len(hist)
    if rows < 200:
        return _failed(
            holding,
            f"周线仅 {rows} 行,< 200 周,无法计算 200w SMA(可能是新上市)",
        )

    # 取最近非 NaN 收盘价:yfinance 对港股/非美股有时当天最新行为 NaN
    close_series = hist["Close"]
    valid_closes = close_series.dropna()
    if valid_closes.empty:
        return _failed(holding, "yfinance Close 列全 NaN(数据源异常)")
    last_close = float(valid_closes.iloc[-1])
    sma_120 = float(close_series.tail(120).mean())
    sma_200 = float(close_series.tail(200).mean())
    delta_120 = (last_close - sma_120) / sma_120
    delta_200 = (last_close - sma_200) / sma_200
    signal = _judge_signal(last_close, sma_120, sma_200)

    logger.info(
        "stocks.signal ticker=%s last=%.2f sma120=%.2f sma200=%.2f signal=%s",
        holding.ticker,
        last_close,
        sma_120,
        sma_200,
        signal,
    )

    return StockSignal(
        holding=holding,
        last_close=last_close,
        sma_120=sma_120,
        sma_200=sma_200,
        delta_120=delta_120,
        delta_200=delta_200,
        signal=signal,
    )


def _failed(holding: Holding, reason: str) -> StockSignal:
    """构造一个失败的 StockSignal"""
    logger.warning("stocks.failed ticker=%s reason=%s", holding.ticker, reason)
    return StockSignal(
        holding=holding,
        last_close=None,
        sma_120=None,
        sma_200=None,
        delta_120=None,
        delta_200=None,
        signal="NONE",
        error=reason,
    )


def fetch_all(holdings: list[Holding]) -> list[StockSignal]:
    """串行拉取所有持仓。12 只规模下 yfinance 串行 ~10-15s,不需要并行。"""
    return [fetch_one(h) for h in holdings]
