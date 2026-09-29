"""
情绪温度计指标采集(模块 5)。

数据源(PLAN 第 4 节模块 5,M3 实施版,见 ADR-0004 决策):
  - CNN Fear & Greed:非官方 JSON 端点
  - VIX:CBOE 官方 historical JSON 主路径,yfinance 备路径(见 ADR-0013)
  - DXY:yfinance DX-Y.NYB
  - Shiller PE:multpl.com 抓取
  - 高收益债利差:FRED BAMLH0A0HYM2

放弃的两个(详见 ADR-0004):
  - 两融余额:Tushare 需注册 + 收费 token,东财抓取脆弱,M3 砍掉
  - 北向资金:PLAN 第 11 节用户已明确放弃

输出:每个指标一份 SentimentMetric,包含当前值 / 前一交易日值 / 变化方向。
M3 不出"一句结论",M4 由 LLM 综合判断。

韧性(ADR-0013):所有 5 个指标都接 last-known-good 缓存。任一外部源
失败时沿用最近一次成功值,saved_at 标记到 SentimentMetric.stale_from,
模板 / LLM prompt 显式区分。超过 7 天的沿用值视为真正失败。
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import yfinance as yf
from bs4 import BeautifulSoup

from src.utils.last_good import LastGoodCache
from src.utils.market_clock import latest_closed_session
from src.utils.retry import retry
from src.utils.secrets import redact_secrets

logger = logging.getLogger(__name__)


def _finite_float(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


@dataclass
class SentimentMetric:
    """单个情绪指标"""
    name: str          # 显示名,如 "CNN Fear & Greed"
    current: float | None
    prior: float | None  # 前一交易日(或最接近的可比值)
    rating: str | None   # 部分指标自带分级文本,如 F&G "greed"
    unit: str = ""       # "" 数字 / "%" 百分比 / "bp" 基点
    error: str | None = None
    stale_from: str | None = None  # ISO 日期;非空表示沿用了 last-known-good 缓存值

    observed_at: str | None = None
    fetched_at: str | None = None
    source: str = ""

    @property
    def delta(self) -> float | None:
        current = _finite_float(self.current)
        prior = _finite_float(self.prior)
        if current is None or prior is None:
            return None
        return current - prior


@dataclass
class SentimentBundle:
    metrics: list[SentimentMetric]
    fetched_at: datetime  # aware UTC


# Frequency rules concern observations, not HTTP success. FRED daily credit
# series has a publication lag; Shiller's underlying earnings are monthly.
_MAX_OBSERVATION_AGE = {"FREDHY": 7, "ShillerPE": 45, "DXY": 4}
METRIC_NAMES = ("CNN Fear & Greed", "VIX", "DXY", "Shiller PE", "高收益债利差")


def _observation_day(raw) -> str:
    if raw is None or isinstance(raw, bool):
        raise ValueError("missing observation date")
    if isinstance(raw, (int, float)):
        raw = datetime.fromtimestamp(raw / 1000 if raw > 10_000_000_000 else raw, UTC)
    stamp = pd.Timestamp(raw)
    if pd.isna(stamp):
        raise ValueError("invalid observation date")
    return stamp.date().isoformat()


def _validate_observation(observed: str | None, key: str, today: date,
                          now: datetime | None = None) -> None:
    if not observed:
        raise ValueError("来源缺少实际观测日期")
    day = date.fromisoformat(observed)
    if key in _MAX_OBSERVATION_AGE:
        if not 0 <= (today - day).days <= _MAX_OBSERVATION_AGE[key]:
            raise ValueError(f"来源观测已过期或超前: {observed}")
        if key == "DXY" and not _dxy_day_completed(day, now or datetime.now(UTC)):
            raise ValueError(f"DXY 观测日尚未完成: {observed}")
    elif day != latest_closed_session("SPY", now or datetime.now(UTC)):
        raise ValueError(f"来源观测不是最近已收盘交易日: {observed}")


def _dxy_day_completed(day: date, now: datetime) -> bool:
    # USDX's FX session runs across midnight, ending at 17:00 New York.
    # Use source-supplied session dates (including FX holidays), not an NYSE
    # calendar. ZoneInfo handles US DST; no fixed UTC or Beijing close time.
    # ICE product/session reference: https://www.ice.com/products/194
    close = datetime.combine(day, time(17), ZoneInfo("America/New_York"))
    return close <= now


def _completed_dxy_values(values, dates, *, source: str, now: datetime | None = None):
    now = now or datetime.now(UTC)
    if not values or len(values) != len(dates):
        raise ValueError("DXY 价格与日期数量不一致")
    days = [_observation_day(raw) for raw in dates]
    if days != sorted(set(days)):
        raise ValueError("DXY 来源日期重复或乱序")
    if any(date.fromisoformat(day) > now.date() + timedelta(days=1) for day in days):
        raise ValueError("DXY 来源日期超前")
    completed = [(v, d) for v, d in zip(values, days, strict=True)
                 if _dxy_day_completed(date.fromisoformat(d), now)]
    if not completed:
        raise ValueError("DXY 无已完成观测")
    # Only unfinished bars may be excluded. A null latest completed close is
    # an error, never an invitation to silently fall back to an earlier bar.
    closes, observed = zip(*completed, strict=True)
    if date.fromisoformat(observed[-1]).weekday() >= 5:
        raise ValueError("DXY 最新观测不是工作日")
    logger.info("sentiment.dxy_completed observed_at=%s unfinished_excluded=%d",
                observed[-1], len(days) - len(observed))
    return _dated_values(list(closes), list(observed), source=source, key="DXY", now=now)


class DatedValues(list):
    def __init__(self, values, *, observed_at: str, source: str):
        super().__init__(values)
        self.observed_at = observed_at
        self.source = source
        self.fetched_at = datetime.now(UTC).isoformat()


def _dated_values(values, dates, *, source: str, key: str, now: datetime | None = None) -> DatedValues:
    if not values or len(values) != len(dates):
        raise ValueError("价格与日期数量不一致")
    days = [_observation_day(raw) for raw in dates]
    if days != sorted(set(days)):
        raise ValueError("来源日期重复或乱序")
    if _finite_float(values[-1]) is None:
        raise ValueError("最新观测无效；不得悄悄使用上一条")
    now = now or datetime.now(UTC)
    _validate_observation(days[-1], key, now.date(), now=now)
    return DatedValues([_finite_float(value) for value in values], observed_at=days[-1], source=source)


# ---------- CNN Fear & Greed ----------
def _ts_to_utc_day(ts: int) -> int:
    """Convert a timestamp (seconds or milliseconds) to a UTC day integer (floor)."""
    if ts > 10_000_000_000:  # 13-digit → milliseconds
        ts //= 1000
    return ts // 86_400


def _cnn_prior_from_historical(historical: list[dict]) -> float | None:
    """从 CNN historical 数组提取前一交易日的值。

    x 可为秒(10 位)或毫秒(13 位)时间戳。historical 可能含日内多条记录。
    策略:按 x 倒排,找第一条与最新记录"日历日(UTC)"不同的条目。
    若所有条目均同日(极端情况),退化为取第 2 条。
    """
    if not historical:
        return None
    sorted_hist = sorted(historical, key=lambda e: (e.get("x") or 0), reverse=True)
    latest_day = _ts_to_utc_day(int(sorted_hist[0].get("x") or 0))
    for entry in sorted_hist[1:]:
        if _ts_to_utc_day(int(entry.get("x") or 0)) != latest_day:
            return entry.get("y")
    # 所有条目同日,退化到第 2 条
    return sorted_hist[1].get("y") if len(sorted_hist) >= 2 else None


@retry(max_attempts=3, base_delay=1.0)
def _fetch_cnn_fear_greed() -> SentimentMetric:
    url = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
    resp = requests.get(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; daily-market-brief/0.1)",
            "Accept": "application/json, text/plain, */*",
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    fg = data.get("fear_and_greed", {}) or {}
    current = fg.get("score")
    rating = fg.get("rating")
    # 优先使用 CNN 自带的 previous_close 字段(前收盘价,最权威)
    prior_raw = fg.get("previous_close")
    # 降级:从历史数组按日历日跨越提取前一交易日值
    if _finite_float(prior_raw) is None:
        historical = data.get("fear_and_greed_historical", {}).get("data", []) or []
        prior_raw = _cnn_prior_from_historical(historical)
    return SentimentMetric(
        name="CNN Fear & Greed",
        observed_at=_observation_day(fg.get("timestamp")),
        fetched_at=datetime.now(UTC).isoformat(), source=url,
        current=_finite_float(current),
        prior=_finite_float(prior_raw),
        rating=str(rating) if rating else None,
    )


# ---------- CBOE 官方 VIX(主路径,见 ADR-0013)----------
# CBOE 是 VIX 的发行方,公开 JSON 端点免 key、不限流(GH IP 段不会被反爬)。
# 比 yfinance 更权威 + 更稳。yfinance 仍保留为备路径。
_CBOE_VIX_URL = "https://cdn.cboe.com/api/global/delayed_quotes/charts/historical/_VIX.json"


@retry(max_attempts=3, base_delay=1.5)
def _fetch_cboe_vix() -> tuple[float, float | None]:
    """从 CBOE historical JSON 拉 ^VIX 最近两个交易日 close。

    返回 (current, prior)。prior 在历史只有 1 行时为 None。失败 raise。
    """
    resp = requests.get(
        _CBOE_VIX_URL,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; daily-market-brief/0.1)",
            "Referer": "https://www.cboe.com/",
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = (resp.json() or {}).get("data") or []
    if not data:
        raise RuntimeError("CBOE VIX 返回空数据")
    tail = data[-5:]
    values = _dated_values([row.get("close") for row in tail],
                           [row.get("date") for row in tail], source=_CBOE_VIX_URL, key="VIX")
    return DatedValues([values[-1], values[-2] if len(values) > 1 else None],
                       observed_at=values.observed_at, source=values.source)


# ---------- yfinance: VIX(备路径) / DXY ----------
# retry 参数与 stocks.py 对齐(base_delay=2.0, backoff=2.5):
# yfinance 在 GH runner 上经常被 Yahoo 限流,弱 retry(1.0s/2.0s)挡不住。
@retry(max_attempts=3, base_delay=2.0, backoff=2.5)
def _fetch_yfinance_close(ticker: str, period: str = "2mo") -> list[float]:
    hist = yf.Ticker(ticker).history(period=period, interval="1d", auto_adjust=False)
    if hist is None or hist.empty:
        raise RuntimeError(f"{ticker} 返回空数据")
    if ticker == "DX-Y.NYB":
        return _completed_dxy_values(hist["Close"].tolist(), list(hist.index), source=f"yfinance:{ticker}")
    return _dated_values(hist["Close"].tolist(), list(hist.index),
                         source=f"yfinance:{ticker}", key="VIX")


@retry(max_attempts=3, base_delay=2.0, backoff=2.5)
def _fetch_yahoo_chart_close(ticker: str, period: str = "2mo") -> list[float]:
    """yfinance 包/cookie 链路故障时的 Yahoo Chart JSON 备路径。"""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
    chart_range = "3mo" if period == "2mo" else period
    resp = requests.get(
        url,
        params={"range": chart_range, "interval": "1d", "events": "history"},
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
        raise RuntimeError(f"{ticker} Yahoo Chart 返回空 result")
    quotes = (((results[0].get("indicators") or {}).get("quote")) or [{}])[0]
    stamps = results[0].get("timestamp") or []
    if ticker == "DX-Y.NYB":
        return _completed_dxy_values(quotes.get("close") or [], stamps, source=url)
    return _dated_values(quotes.get("close") or [], stamps, source=url, key="VIX")


def _fetch_simple_index(ticker: str, display_name: str, unit: str = "") -> SentimentMetric:
    try:
        closes = _fetch_yfinance_close(ticker)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "sentiment.primary_failed ticker=%s source=yfinance exc_type=%s msg=%s",
            ticker,
            type(exc).__name__,
            redact_secrets(str(exc))[:200],
        )
        try:
            closes = _fetch_yahoo_chart_close(ticker)
            logger.warning(
                "sentiment.fallback_used ticker=%s primary=yfinance fallback=yahoo_chart",
                ticker,
            )
        except Exception as fallback_exc:  # noqa: BLE001
            return SentimentMetric(
                name=display_name,
                current=None,
                prior=None,
                rating=None,
                unit=unit,
                error=(
                    f"yfinance {type(exc).__name__}: {exc}; Yahoo Chart "
                    f"{type(fallback_exc).__name__}: {fallback_exc}"
                ),
            )
    if not closes:
        return SentimentMetric(name=display_name, current=None, prior=None, rating=None,
                              unit=unit, error="无数据")
    current = closes[-1]
    # 前一交易日(closes[-2])作为"前一日"参考
    prior = closes[-2] if len(closes) >= 2 else None
    return SentimentMetric(name=display_name, current=current, prior=prior, rating=None, unit=unit,
                           observed_at=closes.observed_at, fetched_at=closes.fetched_at, source=closes.source)

# ---------- multpl.com: Shiller PE ----------
@retry(max_attempts=3, base_delay=1.0)
def _fetch_shiller_pe() -> SentimentMetric:
    url = "https://www.multpl.com/shiller-pe/table/by-month"
    resp = requests.get(url, headers={"User-Agent": "daily-market-brief/0.1"}, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")
    rows = [row.find_all("td") for row in soup.select("#datatable tr") if row.find_all("td")]
    if not rows or len(rows[0]) != 2:
        raise ValueError("multpl dated observation table missing")
    # The first row contains the current published price-based CAPE; older
    # rows are monthly. Do not replace a bad first row with a previous month.
    observed = _observation_day(rows[0][0].get_text(" ", strip=True))
    current = _finite_float(rows[0][1].get_text(" ", strip=True))
    return SentimentMetric(name="Shiller PE", current=current, prior=None, rating=None,
                           observed_at=observed, fetched_at=datetime.now(UTC).isoformat(), source=url)


# ---------- FRED: 高收益债利差 BAMLH0A0HYM2 ----------
@retry(max_attempts=3, base_delay=1.0)
def _fetch_fred_hy_spread(api_key: str) -> SentimentMetric:
    # 注意:api_key 必须通过 params dict 传,**不能拼进 url 字符串**。
    # 否则 requests 抛 HTTPError 时,异常 repr 含完整 url(含 key),
    # @retry 装饰器记 last_exc=%r 会把 key 写到 GH Actions 公开日志。
    url = "https://api.stlouisfed.org/fred/series/observations"
    params = {
        "series_id": "BAMLH0A0HYM2",
        "api_key": api_key,
        "file_type": "json",
        "sort_order": "desc",
        "limit": 10,
    }
    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    obs = data.get("observations", []) or []
    if not obs:
        return SentimentMetric(name="高收益债利差", current=None, prior=None, rating=None,
                              error="FRED 无观测", unit="%")
    # FRED 返回是降序;current 取首个非 "." 值,prior 取约 7 天前
    def _val(o: dict) -> float | None:
        v = o.get("value")
        if v in (None, "", "."):
            return None
        return _finite_float(v)

    current = _val(obs[0])
    # FRED 是降序;obs[1] 即前一观测日(节假日 FRED 不更新即为前一交易日)
    prior = _val(obs[1]) if len(obs) > 1 else None
    return SentimentMetric(name="高收益债利差", current=current, prior=prior, rating=None, unit="%",
                           observed_at=_observation_day(obs[0].get("date")),
                           fetched_at=datetime.now(UTC).isoformat(), source="FRED:BAMLH0A0HYM2")


# ---------- VIX 主路径(CBOE → yfinance)----------
def _fetch_vix_primary() -> SentimentMetric:
    """三层降级里的"原始拉取"层:CBOE 主 → yfinance 备。
    都失败时返回带 error 的 metric,由上层 _with_last_good 决定是否启用缓存值。
    """
    # Layer 1: CBOE 官方源
    try:
        values = _fetch_cboe_vix()
        current, prior = values
        return SentimentMetric(name="VIX", current=current, prior=prior, rating=None,
                               observed_at=values.observed_at, fetched_at=values.fetched_at, source=values.source)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "sentiment.cboe_failed exc_type=%s msg=%s",
            type(exc).__name__, redact_secrets(str(exc))[:200],
        )

    # Layer 2: yfinance 备路径
    return _fetch_simple_index("^VIX", "VIX")


# ---------- last-known-good 包装 ----------
def _with_last_good(
    fn: Callable[[], SentimentMetric],
    *,
    cache: LastGoodCache | None,
    cache_key: str,
    today: date,
) -> SentimentMetric:
    """通用 last-known-good 包装器。

    - 拉取成功(无 error 且 current 有值) → 持久化到 cache,直接返回
    - 拉取失败(error 非空 或 current=None)→ 查 cache:
        - 命中且未过期 → 返回带 stale_from 的 metric(继续参与判断,模板 / LLM 标注)
        - 命中但 > 7 天 → 返回原 error metric(让用户看到真正失败)
        - 未命中 → 返回原 error metric
    """
    m = fn()
    if not m.error and _finite_float(m.current) is not None:
        try:
            _validate_observation(m.observed_at, cache_key, today)
        except ValueError as exc:
            m.current, m.prior, m.error = None, None, str(exc)
    if not m.error and _finite_float(m.current) is not None:
        # 成功:持久化(cache 不可用时悄悄跳过,不影响主流程)
        if cache is not None:
            cache.put(
                f"sentiment.{cache_key}",
                {
                    "current": m.current,
                    "prior": m.prior,
                    "rating": m.rating,
                    "unit": m.unit,
                    "observed_at": m.observed_at, "fetched_at": m.fetched_at, "source": m.source,
                },
                today=today, observed_at=m.observed_at, fetched_at=m.fetched_at, source=m.source,
            )
        return m

    # 失败:尝试 last-known-good
    if cache is None:
        return m
    cached = cache.get(f"sentiment.{cache_key}")
    if not cached:
        return m
    value, saved_at = cached
    if (not isinstance(value, dict) or value.get("observed_at") != saved_at
            or not value.get("source") or LastGoodCache.is_stale(saved_at, today=today)):
        logger.info(
            "sentiment.last_good_stale metric=%s saved_at=%s",
            cache_key, saved_at,
        )
        return m
    if cache_key == "DXY" and not _dxy_day_completed(date.fromisoformat(saved_at), datetime.now(UTC)):
        logger.warning("sentiment.last_good_unfinished metric=DXY observed_at=%s", saved_at)
        return m
    logger.info(
        "sentiment.last_good_used metric=%s saved_at=%s",
        cache_key, saved_at,
    )
    return SentimentMetric(
        name=m.name,
        current=value.get("current") if isinstance(value, dict) else None,
        prior=value.get("prior") if isinstance(value, dict) else None,
        rating=value.get("rating") if isinstance(value, dict) else None,
        unit=value.get("unit", m.unit) if isinstance(value, dict) else m.unit,
        stale_from=saved_at,
        observed_at=saved_at, fetched_at=value.get("fetched_at"), source=value.get("source", ""),
    )


# ---------- 入口 ----------
def fetch_all(
    fred_api_key: str,
    *,
    state_dir: Path | None = None,
    today: date | None = None,
) -> SentimentBundle:
    """采集 5 个情绪指标。

    state_dir 给定时启用 last-known-good 缓存(任一源失败时沿用最近成功值,
    7 天内有效)。state_dir=None 时维持旧行为(失败 → error 字段),仅用于
    单元测试或不需要持久化的场景。

    today 给定时用作"今天"基准(用于 last-good saved_at);默认 date.today()。
    """
    cache = LastGoodCache(state_dir) if state_dir is not None else None
    if today is None:
        today = date.today()

    metrics: list[SentimentMetric] = []
    # 每个指标独立 try-except,失败也不影响其他
    fetchers: list[tuple[str, str, Callable[[], SentimentMetric]]] = [
        # (label, cache_key, fn)
        ("CNN Fear & Greed", "CNN", _fetch_cnn_fear_greed),
        ("VIX",       "VIX",        _fetch_vix_primary),
        ("DXY",       "DXY",        lambda: _fetch_simple_index("DX-Y.NYB", "DXY")),
        ("Shiller PE", "ShillerPE", _fetch_shiller_pe),
        ("高收益债利差", "FREDHY", lambda: _fetch_fred_hy_spread(fred_api_key)),
    ]
    for label, cache_key, fn in fetchers:
        try:
            metrics.append(_with_last_good(fn, cache=cache, cache_key=cache_key, today=today))
        except Exception as exc:  # noqa: BLE001
            # 关键安全:requests/urllib HTTPError 的 str 含完整 url(可能含 ?api_key=xxx),
            # 这个 error 字段会渲染到邮件正文 + 喂给 LLM + 写 state log,必须 redact。
            redacted_msg = redact_secrets(str(exc))[:200]
            logger.error(
                "sentiment.metric_failed label=%s exc_type=%s msg=%s",
                label, type(exc).__name__, redacted_msg,
            )
            # _with_last_good 只在 fn 返回 error metric 时走 cache;
            # 此处兜的是 fn 自己 raise 出来的情况(理论上不会,但防御一下)
            err_metric = SentimentMetric(
                name=label, current=None, prior=None, rating=None,
                error=f"{type(exc).__name__}: {redacted_msg}",
            )
            if cache is not None:
                cached = cache.get(f"sentiment.{cache_key}")
                if (cached and isinstance(cached[0], dict) and cached[0].get("observed_at") == cached[1]
                        and cached[0].get("source") and not LastGoodCache.is_stale(cached[1], today=today)):
                    value, saved_at = cached
                    logger.info(
                        "sentiment.last_good_used_after_raise metric=%s saved_at=%s",
                        cache_key, saved_at,
                    )
                    metrics.append(SentimentMetric(
                        name=label,
                        current=value.get("current") if isinstance(value, dict) else None,
                        prior=value.get("prior") if isinstance(value, dict) else None,
                        rating=value.get("rating") if isinstance(value, dict) else None,
                        unit=value.get("unit", "") if isinstance(value, dict) else "",
                        stale_from=saved_at, observed_at=saved_at,
                        fetched_at=value.get("fetched_at"), source=value.get("source", ""),
                    ))
                    continue
            metrics.append(err_metric)
    return SentimentBundle(metrics=metrics, fetched_at=datetime.now(UTC))
