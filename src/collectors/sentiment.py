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
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import requests
import yfinance as yf
from bs4 import BeautifulSoup

from src.utils.last_good import LastGoodCache
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


# ---------- CNN Fear & Greed ----------
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
    historical = data.get("fear_and_greed_historical", {}).get("data", []) or []
    current = fg.get("score")
    rating = fg.get("rating")
    prior = None
    if historical:
        # 1 天前(前一交易日)的目标毫秒时间戳;在 historical 中找最接近的 (x: ms 时间戳, y: 分值)
        target_ts = (datetime.now(UTC) - timedelta(days=1)).timestamp() * 1000
        best = min(
            historical,
            key=lambda e: abs((e.get("x") or 0) - target_ts),
            default=None,
        )
        prior = (best or {}).get("y")
    return SentimentMetric(
        name="CNN Fear & Greed",
        current=_finite_float(current),
        prior=_finite_float(prior),
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
    # data 按日期升序,只看最后 5 行避免遍历整个 1990 年至今的数组
    tail = data[-5:]
    closes: list[float] = []
    for row in tail:
        c = _finite_float(row.get("close"))
        if c is not None:
            closes.append(c)
    if not closes:
        raise RuntimeError("CBOE VIX close 列无有效数据")
    current = closes[-1]
    prior = closes[-2] if len(closes) >= 2 else None
    return current, prior


# ---------- yfinance: VIX(备路径) / DXY ----------
# retry 参数与 stocks.py 对齐(base_delay=2.0, backoff=2.5):
# yfinance 在 GH runner 上经常被 Yahoo 限流,弱 retry(1.0s/2.0s)挡不住。
@retry(max_attempts=3, base_delay=2.0, backoff=2.5)
def _fetch_yfinance_close(ticker: str, period: str = "2mo") -> list[float]:
    hist = yf.Ticker(ticker).history(period=period, interval="1d", auto_adjust=False)
    if hist is None or hist.empty:
        raise RuntimeError(f"{ticker} 返回空数据")
    closes = [
        close
        for raw in hist["Close"].tolist()
        if (close := _finite_float(raw)) is not None
    ]
    if not closes:
        raise RuntimeError(f"{ticker} Close 列无有效数据")
    return closes


def _fetch_simple_index(ticker: str, display_name: str, unit: str = "") -> SentimentMetric:
    try:
        closes = _fetch_yfinance_close(ticker)
    except Exception as exc:  # noqa: BLE001
        logger.error("sentiment.yf_failed ticker=%s exc_type=%s msg=%s", ticker, type(exc).__name__, redact_secrets(str(exc))[:200])
        return SentimentMetric(name=display_name, current=None, prior=None, rating=None,
                              unit=unit, error=f"{type(exc).__name__}: {exc}")
    if not closes:
        return SentimentMetric(name=display_name, current=None, prior=None, rating=None,
                              unit=unit, error="无数据")
    current = closes[-1]
    # 前一交易日(closes[-2])作为"前一日"参考
    prior = closes[-2] if len(closes) >= 2 else None
    return SentimentMetric(name=display_name, current=current, prior=prior, rating=None, unit=unit)

# ---------- multpl.com: Shiller PE ----------
@retry(max_attempts=3, base_delay=1.0)
def _fetch_shiller_pe() -> SentimentMetric:
    resp = requests.get(
        "https://www.multpl.com/shiller-pe",
        headers={"User-Agent": "Mozilla/5.0 (compatible; daily-market-brief/0.1)"},
        timeout=20,
    )
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")
    current_node = soup.select_one("#current")
    if not current_node:
        return SentimentMetric(name="Shiller PE", current=None, prior=None, rating=None,
                              error="multpl 选择器未命中 #current")
    text = " ".join(current_node.get_text(" ", strip=True).split())
    # 形如:"Current Shiller PE Ratio: 40.53 -0.01 (-0.02%)..."
    m = re.search(r":\s*(\d+(?:\.\d+)?)", text)
    current = float(m.group(1)) if m else None
    return SentimentMetric(name="Shiller PE", current=current, prior=None, rating=None)


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
    return SentimentMetric(name="高收益债利差", current=current, prior=prior, rating=None, unit="%")


# ---------- VIX 主路径(CBOE → yfinance)----------
def _fetch_vix_primary() -> SentimentMetric:
    """三层降级里的"原始拉取"层:CBOE 主 → yfinance 备。
    都失败时返回带 error 的 metric,由上层 _with_last_good 决定是否启用缓存值。
    """
    # Layer 1: CBOE 官方源
    try:
        current, prior = _fetch_cboe_vix()
        return SentimentMetric(name="VIX", current=current, prior=prior, rating=None)
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
        # 成功:持久化(cache 不可用时悄悄跳过,不影响主流程)
        if cache is not None:
            cache.put(
                f"sentiment.{cache_key}",
                {
                    "current": m.current,
                    "prior": m.prior,
                    "rating": m.rating,
                    "unit": m.unit,
                },
                today=today,
            )
        return m

    # 失败:尝试 last-known-good
    if cache is None:
        return m
    cached = cache.get(f"sentiment.{cache_key}")
    if not cached:
        return m
    value, saved_at = cached
    if LastGoodCache.is_stale(saved_at, today=today):
        logger.info(
            "sentiment.last_good_stale metric=%s saved_at=%s",
            cache_key, saved_at,
        )
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
        ("CNN F&G",   "CNN",        _fetch_cnn_fear_greed),
        ("VIX",       "VIX",        _fetch_vix_primary),
        ("DXY",       "DXY",        lambda: _fetch_simple_index("DX-Y.NYB", "DXY")),
        ("ShillerPE", "ShillerPE",  _fetch_shiller_pe),
        ("FRED HY",   "FREDHY",     lambda: _fetch_fred_hy_spread(fred_api_key)),
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
                if cached and not LastGoodCache.is_stale(cached[1], today=today):
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
                        stale_from=saved_at,
                    ))
                    continue
            metrics.append(err_metric)
    return SentimentBundle(metrics=metrics, fetched_at=datetime.now(UTC))
