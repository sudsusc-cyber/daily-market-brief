"""
情绪温度计指标采集(模块 5)。

数据源(PLAN 第 4 节模块 5,M3 实施版,见 ADR-0004 决策):
  - CNN Fear & Greed:非官方 JSON 端点
  - VIX:yfinance ^VIX
  - DXY:yfinance DX-Y.NYB
  - Shiller PE:multpl.com 抓取
  - 高收益债利差:FRED BAMLH0A0HYM2

放弃的两个(详见 ADR-0004):
  - 两融余额:Tushare 需注册 + 收费 token,东财抓取脆弱,M3 砍掉
  - 北向资金:PLAN 第 11 节用户已明确放弃

输出:每个指标一份 SentimentMetric,包含当前值 / 前一交易日值 / 变化方向。
M3 不出"一句结论",M4 由 LLM 综合判断。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import requests
import yfinance as yf
from bs4 import BeautifulSoup

from src.utils.retry import retry
from src.utils.secrets import redact_secrets

logger = logging.getLogger(__name__)


@dataclass
class SentimentMetric:
    """单个情绪指标"""
    name: str          # 显示名,如 "CNN Fear & Greed"
    current: float | None
    prior: float | None  # 前一交易日(或最接近的可比值)
    rating: str | None   # 部分指标自带分级文本,如 F&G "greed"
    unit: str = ""       # "" 数字 / "%" 百分比 / "bp" 基点
    error: str | None = None

    @property
    def delta(self) -> float | None:
        if self.current is None or self.prior is None:
            return None
        return self.current - self.prior


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
        current=float(current) if current is not None else None,
        prior=float(prior) if prior is not None else None,
        rating=str(rating) if rating else None,
    )


# ---------- yfinance: VIX / DXY ----------
@retry(max_attempts=3, base_delay=1.0)
def _fetch_yfinance_close(ticker: str, period: str = "2mo") -> list[float]:
    hist = yf.Ticker(ticker).history(period=period, interval="1d", auto_adjust=False)
    if hist is None or hist.empty:
        raise RuntimeError(f"{ticker} 返回空数据")
    return [float(v) for v in hist["Close"].tolist()]


def _fetch_simple_index(ticker: str, display_name: str, unit: str = "") -> SentimentMetric:
    try:
        closes = _fetch_yfinance_close(ticker)
    except Exception as exc:  # noqa: BLE001
        logger.exception("sentiment.yf_failed ticker=%s", ticker)
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
        try:
            return float(v)
        except ValueError:
            return None

    current = _val(obs[0])
    # FRED 是降序;obs[1] 即前一观测日(节假日 FRED 不更新即为前一交易日)
    prior = _val(obs[1]) if len(obs) > 1 else None
    return SentimentMetric(name="高收益债利差", current=current, prior=prior, rating=None, unit="%")


# ---------- 入口 ----------
def fetch_all(fred_api_key: str) -> SentimentBundle:
    metrics: list[SentimentMetric] = []
    # 每个指标独立 try-except,失败也不影响其他
    fetchers: list[tuple[str, Callable[[], SentimentMetric]]] = [
        ("CNN F&G", _fetch_cnn_fear_greed),
        ("VIX",     lambda: _fetch_simple_index("^VIX", "VIX")),
        ("DXY",     lambda: _fetch_simple_index("DX-Y.NYB", "DXY")),
        ("ShillerPE", _fetch_shiller_pe),
        ("FRED HY", lambda: _fetch_fred_hy_spread(fred_api_key)),
    ]
    for label, fn in fetchers:
        try:
            metrics.append(fn())
        except Exception as exc:  # noqa: BLE001
            # 关键安全:requests/urllib HTTPError 的 str 含完整 url(可能含 ?api_key=xxx),
            # 这个 error 字段会渲染到邮件正文 + 喂给 LLM + 写 state log,必须 redact。
            redacted_msg = redact_secrets(str(exc))[:200]
            logger.error(
                "sentiment.metric_failed label=%s exc_type=%s msg=%s",
                label, type(exc).__name__, redacted_msg,
            )
            metrics.append(SentimentMetric(
                name=label, current=None, prior=None, rating=None,
                error=f"{type(exc).__name__}: {redacted_msg}",
            ))
    return SentimentBundle(metrics=metrics, fetched_at=datetime.now(UTC))
