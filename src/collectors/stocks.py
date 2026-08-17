"""
持仓股票信号采集:拉周线 → 算 120w/200w SMA → 判断 DCA / LUMP-SUM。

数据源:yfinance,周频,5 年历史。
失败处理:不抛异常,在 StockSignal.error 字段记录,让上层渲染时区分展示。
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Literal

import requests
import yfinance as yf

from src.config import Holding
from src.utils.retry import retry
from src.utils.secrets import redact_secrets

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
    data_source: str = ""


def _judge_signal(last_close: float, sma_120: float, sma_200: float) -> SignalKind:
    """信号判断:200w 优先于 120w(更深的折扣)"""
    if last_close <= sma_200:
        return "LUMP_SUM"
    if last_close <= sma_120:
        return "DCA"
    return "NONE"


@retry(max_attempts=3, base_delay=2.0, backoff=2.5)
def _yf_history(ticker: yf.Ticker):
    """yfinance 周线拉取,带重试退避(限流时 2s/5s/12s 三次重试)。

    yfinance 偶尔返回空 DataFrame 而不抛异常（Yahoo 端间歇性问题）；
    此处显式 raise 让 @retry 退避重试，避免一次空响应就判为失败。"""
    hist = ticker.history(period="5y", interval="1wk", auto_adjust=False)
    if hist is None or hist.empty:
        raise RuntimeError(f"yfinance 返回空数据 for {ticker.ticker}")
    return hist


@retry(max_attempts=3, base_delay=2.0, backoff=2.5)
def _yahoo_chart_weekly(symbol: str) -> tuple[list[float], float | None]:
    """yfinance 库路径故障时，直连 Yahoo Chart JSON 的备路径。

    两者的底层数据同源，但认证/cookie/库版本链路不同；
    这能覆盖 yfinance 包回归、crumb 故障和空 DataFrame，同时
    保持与主路径一致的复权/交易所口径。
    """
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    resp = requests.get(
        url,
        params={
            "range": "5y",
            "interval": "1wk",
            "events": "history",
            "includeAdjustedClose": "true",
        },
        headers={"User-Agent": "Mozilla/5.0 (compatible; daily-market-brief/0.1)"},
        timeout=(10, 20),
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Yahoo Chart HTTP {resp.status_code}")
    chart = (resp.json() or {}).get("chart") or {}
    if chart.get("error"):
        raise RuntimeError(f"Yahoo Chart API error: {chart['error']}")
    results = chart.get("result") or []
    if not results:
        raise RuntimeError("Yahoo Chart 返回空 result")
    result = results[0]
    quotes = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    closes = [
        float(value)
        for value in quotes.get("close") or []
        if value is not None and math.isfinite(float(value)) and float(value) > 0
    ]
    if len(closes) < 200:
        raise RuntimeError(f"Yahoo Chart 周线仅 {len(closes)} 行")
    live_raw = (result.get("meta") or {}).get("regularMarketPrice")
    try:
        live_price = float(live_raw)
    except (TypeError, ValueError):
        live_price = None
    if live_price is not None and (not math.isfinite(live_price) or live_price <= 0):
        live_price = None
    return closes, live_price


def _build_signal(
    holding: Holding,
    *,
    closes: list[float],
    live_price: float | None,
    data_source: str,
) -> StockSignal:
    if len(closes) < 200:
        raise RuntimeError(f"周线仅 {len(closes)} 行,< 200 周")
    sma_120 = sum(closes[-120:]) / 120
    sma_200 = sum(closes[-200:]) / 200
    weekly_close = closes[-1]
    if not all(
        math.isfinite(value) and value > 0
        for value in (sma_120, sma_200, weekly_close)
    ):
        raise RuntimeError("周线价格或均线无效")
    last_close = live_price if live_price is not None else weekly_close
    delta_120 = (last_close - sma_120) / sma_120
    delta_200 = (last_close - sma_200) / sma_200
    signal = _judge_signal(last_close, sma_120, sma_200)
    logger.info(
        "stocks.signal ticker=%s source=%s last=%.2f sma120=%.2f sma200=%.2f signal=%s",
        holding.ticker,
        data_source,
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
        data_source=data_source,
    )


def fetch_one(holding: Holding) -> StockSignal:
    """
    拉单只股票的周线并计算信号。

    任何异常都会被吞掉并写入 error,保证上层批处理不会因单只失败中断。
    周线不足 200 周(新股)按"数据不足"处理,error 字段说明原因。

    last_close 优先取 fast_info.last_price（当日/最新价），
    SMA 计算始终基于周线数据。
    """
    symbol = holding.yfinance_symbol
    primary_error: Exception | None = None
    try:
        ticker = yf.Ticker(symbol)
        hist = _yf_history(ticker)
        if hist is None or hist.empty or "Close" not in hist:
            raise RuntimeError("yfinance 返回空数据")
        if len(hist) < 200:
            raise RuntimeError(f"yfinance 周线仅 {len(hist)} 行")
        closes = [
            float(value)
            for value in hist["Close"].tolist()
            if value is not None and math.isfinite(float(value)) and float(value) > 0
        ]
        try:
            live_price: float | None = float(ticker.fast_info.last_price)
        except Exception:  # noqa: BLE001 — 取不到 live 价是已知降级路径
            live_price = None
        if live_price is not None and (not math.isfinite(live_price) or live_price <= 0):
            live_price = None
        return _build_signal(
            holding,
            closes=closes,
            live_price=live_price,
            data_source="yfinance",
        )
    except Exception as exc:  # noqa: BLE001
        primary_error = exc
        logger.warning(
            "stocks.primary_failed ticker=%s source=yfinance exc_type=%s msg=%s",
            holding.ticker,
            type(exc).__name__,
            redact_secrets(str(exc))[:200],
        )

    try:
        closes, live_price = _yahoo_chart_weekly(symbol)
        signal = _build_signal(
            holding,
            closes=closes,
            live_price=live_price,
            data_source="yahoo_chart",
        )
        logger.warning(
            "stocks.fallback_used ticker=%s primary=yfinance fallback=yahoo_chart",
            holding.ticker,
        )
        return signal
    except Exception as fallback_exc:  # noqa: BLE001
        return _failed(
            holding,
            "行情主备链路均失败: "
            f"yfinance {type(primary_error).__name__}: {primary_error}; "
            f"Yahoo Chart {type(fallback_exc).__name__}: {fallback_exc}",
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
    """串行拉取所有持仓。14 只规模下 yfinance 串行 ~12-18s,不需要并行。"""
    return [fetch_one(h) for h in holdings]
