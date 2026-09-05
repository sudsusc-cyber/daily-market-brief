"""
持仓信号：按逐股九五策略计算 120w / 200w / 250d SMA。

数据源:yfinance,5 年周线；NVDA/TSM 另取 2 年日线。
失败处理:不抛异常,在 StockSignal.error 字段记录,让上层渲染时区分展示。
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

import pandas as pd
import requests
import yfinance as yf

from src.config import BuyLine, BuyStrategy, Holding, buy_strategy
from src.utils.market_clock import validate_history, validate_quote
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
    sma_250d: float | None = None
    observed_at: str | None = None

    @property
    def strategy(self) -> BuyStrategy:
        return buy_strategy(self.holding.ticker)

    def line_value(self, line: BuyLine) -> float | None:
        return {"120w": self.sma_120, "200w": self.sma_200, "250d": self.sma_250d}[line]

    @property
    def buy_lines(self) -> list[dict]:
        """实际参与信号的买入线视图，供引言使用；不包含纯观察参考线。"""
        strategy = self.strategy
        lines = (
            (strategy.dca_line, strategy.lump_line)
            if strategy.dca_line else (strategy.lump_line,)
        )
        result = []
        for line, label in zip(lines, strategy.line_labels, strict=True):
            value = self.line_value(line)
            delta = (self.last_close - value) / value if value and self.last_close else None
            result.append({"label": label, "value": value, "delta": delta})
        return result

    @property
    def reference_lines(self) -> list[dict]:
        """邮件统一双数值展示；单线组的 120 周仅作观察，不参与信号。"""
        lines = self.buy_lines
        if self.strategy.dca_line is None:
            value = self.sma_120
            delta = (self.last_close - value) / value if value and self.last_close else None
            lines.insert(0, {"label": "120 周", "value": value, "delta": delta})
        return lines


def _judge_signal(
    last_close: float,
    sma_120: float | None,
    sma_200: float | None,
    *,
    ticker: str = "MSFT",
    sma_250d: float | None = None,
) -> SignalKind:
    """每日持续显示所在区间；大额层优先，不按均线数值重新排列策略。"""
    strategy = buy_strategy(ticker)
    averages = {"120w": sma_120, "200w": sma_200, "250d": sma_250d}
    required = [last_close, averages[strategy.lump_line]]
    if strategy.dca_line:
        required.append(averages[strategy.dca_line])
    if any(value is None or not math.isfinite(value) or value <= 0 for value in required):
        raise ValueError("策略所需行情或买入线缺失/无效")
    if last_close <= averages[strategy.lump_line]:
        return "LUMP_SUM"
    if strategy.dca_line and last_close <= averages[strategy.dca_line]:
        return "DCA"
    return "NONE"


@retry(max_attempts=3, base_delay=2.0, backoff=2.5)
def _yf_history(ticker: yf.Ticker):
    """yfinance 周线拉取,带重试退避(限流时 2s/5s/12s 三次重试)。

    yfinance 偶尔返回空 DataFrame 而不抛异常（Yahoo 端间歇性问题）；
    此处显式 raise 让 @retry 退避重试，避免一次空响应就判为失败。"""
    hist = ticker.history(period="5y", interval="1wk", auto_adjust=False, timeout=20)
    if hist is None or hist.empty:
        raise RuntimeError(f"yfinance 返回空数据 for {ticker.ticker}")
    return hist


@retry(max_attempts=3, base_delay=2.0, backoff=2.5)
def _yf_daily_history(ticker: yf.Ticker):
    """250 日线使用 250 个真实交易日，不能用 50 周近似。"""
    hist = ticker.history(period="2y", interval="1d", auto_adjust=False, timeout=20)
    if hist is None or hist.empty:
        raise RuntimeError(f"yfinance 返回空日线 for {ticker.ticker}")
    return hist


def _yahoo_chart_history(
    symbol: str, *, interval: str, period: str, minimum: int,
) -> tuple[list[float], float | None]:
    """yfinance 库路径故障时，直连 Yahoo Chart JSON 的备路径。

    两者的底层数据同源，但认证/cookie/库版本链路不同；
    这能覆盖 yfinance 包回归、crumb 故障和空 DataFrame，同时
    保持与主路径一致的复权/交易所口径。
    """
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    resp = requests.get(
        url,
        params={
            "range": period,
            "interval": interval,
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
    now = datetime.now(UTC)
    stamps = result.get("timestamp") or []
    index = pd.to_datetime(stamps, unit="s", utc=True)
    observed = _validate_history_window(index, symbol=symbol, interval=interval, now=now)
    quotes = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    closes = [float(value) if value is not None else float("nan")
              for value in quotes.get("close") or []]
    if len(stamps) != len(closes):
        raise ValueError("行情时间戳与价格数量不一致")
    quote_day = validate_quote((result.get("meta") or {}).get("regularMarketTime"), symbol=symbol, now=now)
    closes = PriceHistory(closes, observed_at=quote_day.isoformat(), bar_date=observed.isoformat())
    _checked_mean(closes, minimum)
    live_raw = (result.get("meta") or {}).get("regularMarketPrice")
    try:
        live_price = float(live_raw)
    except (TypeError, ValueError):
        live_price = None
    if live_price is not None and (not math.isfinite(live_price) or live_price <= 0):
        live_price = None
    return closes, live_price


@retry(max_attempts=3, base_delay=2.0, backoff=2.5)
def _yahoo_chart_weekly(symbol: str) -> tuple[list[float], float | None]:
    return _yahoo_chart_history(symbol, interval="1wk", period="5y", minimum=120)


@retry(max_attempts=3, base_delay=2.0, backoff=2.5)
def _yahoo_chart_daily(symbol: str) -> tuple[list[float], float | None]:
    return _yahoo_chart_history(symbol, interval="1d", period="2y", minimum=250)


def _checked_mean(closes: list[float], periods: int) -> float:
    if len(closes) < periods:
        raise ValueError(f"行情仅 {len(closes)} 行，需要 {periods} 行")
    window = closes[-periods:]
    if any(not math.isfinite(value) or value <= 0 for value in window):
        raise ValueError("均线窗口含无效价格，不能删除缺失项后用更旧价格凑数")
    return sum(window) / periods


class PriceHistory(list):
    def __init__(self, values, *, observed_at: str, bar_date: str | None = None):
        super().__init__(values)
        self.observed_at = observed_at
        self.bar_date = bar_date or observed_at


def _validate_history_window(index, *, symbol: str, interval: str, now: datetime):
    # Missing rows outside this strategy's active window cannot affect its
    # averages. In particular, growth holdings need 120 weeks, not 200 weeks.
    periods = 250 if interval == "1d" else (120 if buy_strategy(symbol).dca_line == "250d" else 200)
    return validate_history(index[-periods:], symbol=symbol, interval=interval, now=now)


def _history_closes(hist, *, symbol: str, interval: str = "1wk") -> list[float]:
    if hist is None or hist.empty or "Close" not in hist:
        raise ValueError("yfinance 返回空行情")
    observed = _validate_history_window(hist.index, symbol=symbol, interval=interval, now=datetime.now(UTC))
    return PriceHistory([float(value) if value is not None else float("nan")
                         for value in hist["Close"].tolist()], observed_at=observed.isoformat())


def _build_signal(
    holding: Holding,
    *,
    closes: list[float],
    live_price: float | None,
    data_source: str,
    daily_closes: list[float] | None = None,
) -> StockSignal:
    strategy = buy_strategy(holding.ticker)
    needs_daily = strategy.dca_line == "250d"
    _checked_mean(closes, 120 if needs_daily else 200)
    sma_120 = _checked_mean(closes, 120)
    try:
        sma_200 = _checked_mean(closes, 200)
    except ValueError:
        if not needs_daily:
            raise
        # 成长组只依赖 120 周；更早的无效周线不能阻断有效的买入线。
        sma_200 = None
    sma_250d = _checked_mean(daily_closes or [], 250) if needs_daily else None
    weekly_close = closes[-1]
    if live_price is not None and (not math.isfinite(live_price) or live_price <= 0):
        live_price = None
    fallback_close = daily_closes[-1] if needs_daily else weekly_close
    last_close = live_price if live_price is not None else fallback_close
    delta_120 = (last_close - sma_120) / sma_120
    delta_200 = (last_close - sma_200) / sma_200 if sma_200 else None
    signal = _judge_signal(
        last_close, sma_120, sma_200, ticker=holding.ticker, sma_250d=sma_250d,
    )
    logger.info(
        "stocks.signal ticker=%s source=%s last=%.2f sma120=%.2f sma200=%s signal=%s strategy=%s sma250d=%s",
        holding.ticker,
        data_source,
        last_close,
        sma_120,
        sma_200,
        signal,
        strategy.key,
        sma_250d,
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
        sma_250d=sma_250d,
        observed_at=getattr(closes, "observed_at", None),
    )


def fetch_one(holding: Holding) -> StockSignal:
    """
    拉单只股票所需的周线/日线并计算分组信号。

    任何异常都会被吞掉并写入 error,保证上层批处理不会因单只失败中断。
    不足策略要求的 120/200 周或 250 个交易日时按数据不足处理。

    last_close 优先取 fast_info.last_price（当日/最新价），
    周线 SMA 与日线 SMA 分开计算，主备数据源保持相同 Close 口径。
    """
    symbol = holding.yfinance_symbol
    daily_closes: list[float] | None = None
    daily_source = ""
    if buy_strategy(holding.ticker).dca_line == "250d":
        try:
            daily_closes = _history_closes(_yf_daily_history(yf.Ticker(symbol)), symbol=symbol, interval="1d")
            _checked_mean(daily_closes, 250)
            daily_source = "+daily:yfinance"
        except Exception as exc:  # noqa: BLE001
            logger.warning("stocks.daily_primary_failed ticker=%s type=%s", holding.ticker, type(exc).__name__)
            try:
                daily_closes, _ = _yahoo_chart_daily(symbol)
                _checked_mean(daily_closes, 250)
                daily_source = "+daily:yahoo_chart"
            except Exception as daily_exc:  # noqa: BLE001
                return _failed(holding, f"250 日线主备链路均失败: {redact_secrets(str(daily_exc))[:180]}")
    primary_error: Exception | None = None
    try:
        ticker = yf.Ticker(symbol)
        hist = _yf_history(ticker)
        closes = _history_closes(hist, symbol=symbol)
        metadata = ticker.history_metadata
        if not isinstance(metadata, dict):
            raise ValueError("行情缺少报价时间元数据")
        closes.observed_at = validate_quote(metadata.get("regularMarketTime"), symbol=symbol,
                                           now=datetime.now(UTC)).isoformat()
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
            data_source="yfinance" + daily_source,
            daily_closes=daily_closes,
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
            data_source="yahoo_chart" + daily_source,
            daily_closes=daily_closes,
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
    reason = redact_secrets(reason)
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


def fetch_all(
    holdings: list[Holding], *, on_result: Callable[[StockSignal], None] | None = None,
) -> list[StockSignal]:
    """串行拉取持仓，并逐只报告进度供总时限降级保留已完成结果。"""
    results = []
    for holding in holdings:
        result = fetch_one(holding)
        results.append(result)
        if on_result is not None:
            on_result(result)
    return results
