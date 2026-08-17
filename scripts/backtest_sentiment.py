"""用真实历史数据检查情绪温度计五个档位的可达性。

这是人工校准工具,不在每日发信链路中运行。CNN 官方端点不提供稳定的批量
下载,因此回测使用每周归档的 CNN 历史镜像;仅使用 2021-02-01 之后的
数据。其他序列与生产公式的来源/定义一致。
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
from bisect import bisect_right
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.collectors.sentiment import SentimentBundle, SentimentMetric  # noqa: E402
from src.processors.sentiment_judge import score_sentiment  # noqa: E402

_CNN_ARCHIVE_URL = (
    "https://raw.githubusercontent.com/whit3rabbit/fear-greed-data/"
    "main/fear-greed.csv"
)
_VIX_URL = "https://cdn.cboe.com/api/global/delayed_quotes/charts/historical/_VIX.json"
_HY_ARCHIVE_URL = (
    "https://raw.githubusercontent.com/maaurocp/Trading_Protocol/"
    "bf64e83fa4c2a6e72c37d3883476dc81bd9d2e31/"
    "data/raw/fred_BAMLH0A0HYM2.csv"
)
_SHILLER_URL = "https://www.multpl.com/shiller-pe/table/by-month"
_YAHOO_DXY_URL = "https://query1.finance.yahoo.com/v8/finance/chart/DX-Y.NYB"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; daily-market-brief-backtest/0.1)",
    "Referer": "https://www.cboe.com/",
}
_LABELS = ("极度恐慌", "偏冷", "中性", "偏热", "极度贪婪")


def _get(url: str, **kwargs: object) -> requests.Response:
    response = requests.get(url, headers=_HEADERS, timeout=45, **kwargs)
    response.raise_for_status()
    return response


def _parse_iso(raw: str) -> date:
    return date.fromisoformat(raw[:10])


def _cnn_history() -> dict[date, float]:
    rows = csv.DictReader(io.StringIO(_get(_CNN_ARCHIVE_URL).text))
    return {_parse_iso(row["Date"]): float(row["Fear Greed"]) for row in rows}


def _vix_history() -> dict[date, float]:
    rows = (_get(_VIX_URL).json() or {}).get("data") or []
    return {_parse_iso(row["date"]): float(row["close"]) for row in rows}


def _hy_history() -> dict[date, float]:
    rows = csv.DictReader(io.StringIO(_get(_HY_ARCHIVE_URL).text))
    history: dict[date, float] = {}
    for row in rows:
        raw = (row.get("BAMLH0A0HYM2") or "").strip()
        if raw and raw != ".":
            history[_parse_iso(row["date"])] = float(raw)
    return history


def _dxy_history(start: date, end: date) -> dict[date, float]:
    period1 = int(datetime.combine(start, datetime.min.time(), tzinfo=UTC).timestamp())
    period2 = int(
        datetime.combine(end + timedelta(days=2), datetime.min.time(), tzinfo=UTC).timestamp()
    )
    data = _get(
        _YAHOO_DXY_URL,
        params={
            "period1": period1,
            "period2": period2,
            "interval": "1d",
            "events": "history",
        },
    ).json()
    result = ((data.get("chart") or {}).get("result") or [])[0]
    closes = (((result.get("indicators") or {}).get("quote") or [{}])[0]).get("close") or []
    history: dict[date, float] = {}
    for timestamp, close in zip(result.get("timestamp") or [], closes, strict=False):
        if close is not None:
            history[datetime.fromtimestamp(timestamp, UTC).date()] = float(close)
    return history


def _shiller_history() -> dict[date, float]:
    soup = BeautifulSoup(_get(_SHILLER_URL).text, "lxml")
    history: dict[date, float] = {}
    for row in soup.select("table tr"):
        cells = row.find_all(["th", "td"])
        if len(cells) < 2:
            continue
        try:
            observed = datetime.strptime(cells[0].get_text(strip=True), "%b %d, %Y").date()
            history[observed] = float(cells[1].get_text(strip=True).replace(",", ""))
        except ValueError:
            continue
    return history


class _AsOfSeries:
    """按生产口径使用当日或之前最近的已发布值。"""

    def __init__(self, values: dict[date, float]) -> None:
        self.values = values
        self.dates = sorted(values)

    def get(self, day: date) -> float | None:
        position = bisect_right(self.dates, day) - 1
        if position < 0:
            return None
        return self.values[self.dates[position]]


def _bundle(day: date, values: dict[str, float]) -> SentimentBundle:
    return SentimentBundle(
        metrics=[
            SentimentMetric("CNN Fear & Greed", values["cnn"], None, None),
            SentimentMetric("VIX", values["vix"], None, None),
            SentimentMetric("高收益债利差", values["hy"], None, None, unit="%"),
            SentimentMetric("Shiller PE", values["pe"], None, None),
            SentimentMetric("DXY", values["dxy"], None, None),
        ],
        fetched_at=datetime.combine(day, datetime.min.time(), tzinfo=UTC),
    )


def run(start: date, end: date) -> list[tuple[date, float, str]]:
    cnn = _cnn_history()
    vix = _vix_history()
    hy = _AsOfSeries(_hy_history())
    dxy = _AsOfSeries(_dxy_history(start, end))
    pe = _AsOfSeries(_shiller_history())
    results: list[tuple[date, float, str]] = []
    for day in sorted(set(cnn) & set(vix)):
        if not start <= day <= end:
            continue
        values = {
            "cnn": cnn[day],
            "vix": vix[day],
            "hy": hy.get(day),
            "dxy": dxy.get(day),
            "pe": pe.get(day),
        }
        if any(value is None for value in values.values()):
            continue
        scored = score_sentiment(_bundle(day, values))  # type: ignore[arg-type]
        if scored:
            results.append((day, scored["score"], scored["verdict"]))
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=_parse_iso, default=date(2021, 2, 1))
    parser.add_argument("--end", type=_parse_iso, default=date.today())
    args = parser.parse_args()
    results = run(args.start, args.end)
    if not results:
        raise SystemExit("没有可用的共同交易日")

    counts = Counter(verdict for _, _, verdict in results)
    low = min(results, key=lambda row: row[1])
    high = max(results, key=lambda row: row[1])
    print(f"共同交易日: {len(results)} ({results[0][0]} → {results[-1][0]})")
    for label in _LABELS:
        count = counts[label]
        print(f"{label}: {count} 天 ({count / len(results):.2%})")
    print(f"最低: {low[0]} / {low[1]:.1f} / {low[2]}")
    print(f"最高: {high[0]} / {high[1]:.1f} / {high[2]}")


if __name__ == "__main__":
    main()
