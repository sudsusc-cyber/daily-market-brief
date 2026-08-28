"""无需登录的 Morningstar 公允价值发现、校验与短时回退。"""

from __future__ import annotations

import importlib
import json
import logging
import math
import os
import re
import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_CACHE_NAME = "morningstar_fair_values.json"
_MAX_CACHE_AGE = timedelta(hours=48)
_GOOGLE_NEWS = "https://news.google.com/rss/search"
_JINA_READER = "https://r.jina.ai/http://"
_USER_AGENT = "daily-market-brief/1.0 (+public-source-validation)"


@dataclass(frozen=True)
class MorningstarSecurity:
    ticker: str
    provider_code: str
    currency: str
    company_name: str
    news_query: str
    curated_urls: tuple[str, ...] = ()
    curated_dates: tuple[str, ...] = ()


SECURITIES: dict[str, MorningstarSecurity] = {
    "MSFT": MorningstarSecurity(
        "MSFT",
        "XNAS:MSFT",
        "USD",
        "Microsoft",
        '"Microsoft" "Fair Value Estimate" source:Morningstar',
        (
            "https://global.morningstar.com/en-nd/stocks/"
            "after-earnings-is-microsoft-stock-buy-sell-or-fairly-valued-6",
        ),
        ("2026-08-11",),
    ),
    "COST": MorningstarSecurity(
        "COST",
        "XNAS:COST",
        "USD",
        "Costco",
        '"Costco" "Fair Value Estimate" source:Morningstar',
        (
            "https://www.morningstar.com/stocks/"
            "costco-earnings-margin-expansion-membership-growth-shine",
        ),
        ("2026-03-06",),
    ),
    "AAPL": MorningstarSecurity(
        "AAPL",
        "XNAS:AAPL",
        "USD",
        "Apple",
        '"Apple" "Fair Value Estimate" source:Morningstar',
        (
            "https://www.morningstar.com/stocks/"
            "after-earnings-is-apple-stock-buy-sell-or-fairly-valued-11",
        ),
        ("2026-08-07",),
    ),
    "NVDA": MorningstarSecurity(
        "NVDA",
        "XNAS:NVDA",
        "USD",
        "Nvidia",
        '"Nvidia" "Fair Value Estimate" source:Morningstar',
        (
            "https://global.morningstar.com/en-ca/stocks/"
            "going-into-earnings-is-nvidia-stock-buy-sell-or-fairly-valued-4",
        ),
        ("2026-08-20",),
    ),
    "TSM": MorningstarSecurity(
        "TSM",
        "XNYS:TSM",
        "USD",
        "Taiwan Semiconductor",
        '"Taiwan Semiconductor" Morningstar "fair value"',
        (
            "https://www.morningstar.com/stocks/"
            "taiwan-semiconductor-earnings-raising-fair-value-estimate-after-beat-and-raise-quarter",
        ),
        ("2026-07-16",),
    ),
    "MCO": MorningstarSecurity(
        "MCO",
        "XNYS:MCO",
        "USD",
        "Moody",
        '"Moody’s" Morningstar "fair value"',
        ("https://www.morningstar.com/stocks/35-new-4-star-stocks-this-week",),
        ("2026-05-18",),
    ),
    "GOOG": MorningstarSecurity(
        "GOOG",
        "XNAS:GOOG",
        "USD",
        "Alphabet",
        '"Alphabet" "Fair Value Estimate" source:Morningstar',
        (
            "https://global.morningstar.com/en-nd/stocks/"
            "alphabet-earnings-strong-ai-monetization-continues-spearhead-results",
        ),
        ("2026-07-23",),
    ),
    "BRK.B": MorningstarSecurity(
        "BRK.B",
        "XNYS:BRK.B",
        "USD",
        "Berkshire Hathaway",
        '"Berkshire Hathaway" "Class B" "fair value" source:Morningstar',
        (
            "https://www.morningstar.com/stocks/"
            "berkshire-hathaway-buffetts-retirement-does-not-alter-our-outlook-companys-future",
        ),
        ("2026-01-04",),
    ),
    "KO": MorningstarSecurity(
        "KO",
        "XNYS:KO",
        "USD",
        "Coca-Cola",
        '"Coca-Cola" "Fair Value Estimate" source:Morningstar',
        (
            "https://global.morningstar.com/en-eu/stocks/"
            "after-earnings-is-coca-cola-stock-buy-sell-or-fairly-valued-4",
        ),
        ("2026-05-08",),
    ),
    "AXP": MorningstarSecurity(
        "AXP",
        "XNYS:AXP",
        "USD",
        "American Express",
        '"American Express" "Fair Value Estimate" source:Morningstar',
        (
            "https://www.morningstar.com/stocks/"
            "american-express-earnings-revenue-growth-exceeds-our-expectations-is-offset-by-higher-expenses",
        ),
        ("2026-07-24",),
    ),
    "0700.HK": MorningstarSecurity(
        "0700.HK",
        "XHKG:00700",
        "HKD",
        "Tencent",
        '"Tencent" Morningstar "fair value"',
        (
            "https://global.morningstar.com/en-nd/stocks/"
            "tencent-earnings-broad-based-strength-with-emerging-ai-upside",
        ),
        ("2025-08-14",),
    ),
    "9992.HK": MorningstarSecurity(
        "9992.HK",
        "XHKG:09992",
        "HKD",
        "Pop Mart",
        '"Pop Mart" Morningstar "fair value"',
        ("https://theedgemalaysia.com/node/788636",),
        ("2026-01-14",),
    ),
    "MA": MorningstarSecurity(
        "MA",
        "XNYS:MA",
        "USD",
        "Mastercard",
        '"Mastercard" "Fair Value Estimate" source:Morningstar',
        (
            "https://www.morningstar.com/stocks/"
            "mastercard-earnings-solid-consumer-spending-leads-steady-growth",
        ),
        ("2026-07-30",),
    ),
    "LIN": MorningstarSecurity(
        "LIN",
        "XNAS:LIN",
        "USD",
        "Linde",
        '"Linde" "Fair Value Estimate" source:Morningstar',
        (
            "https://www.morningstar.com/stocks/"
            "basic-materials-sector-rises-underperforms-leaving-opportunities-chemicals-agriculture",
        ),
        ("2026-07-09",),
    ),
}


@dataclass(frozen=True)
class MorningstarFairValue:
    ticker: str
    provider_code: str
    fair_value: float
    currency: str
    rating_type: str
    fair_value_updated_at: str
    retrieved_at: str
    source_provider: str
    source_url: str
    observation_count: int = 2
    fallback_used: bool = False
    warning: str | None = None


class MorningstarProvider(Protocol):
    def fetch_all(
        self,
        securities: Mapping[str, MorningstarSecurity],
        *,
        checked_at: datetime,
    ) -> tuple[dict[str, MorningstarFairValue], dict[str, str]]: ...


@dataclass(frozen=True)
class _Candidate:
    url: str
    published_at: datetime | None


def _normalise_date(value: str) -> str:
    text = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text
    return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()


def _currency(context: str, expected: str) -> str:
    upper = context.upper()
    if re.search(r"\bHKD\b|HK\$", upper):
        found = "HKD"
    elif re.search(r"\bUSD\b|US\$|\$", upper):
        found = "USD"
    else:
        found = expected
    if found != expected:
        raise ValueError(f"来源币种 {found} 与固定上市口径 {expected} 不符")
    return found


def _first_match(patterns: tuple[str, ...], text: str) -> re.Match[str] | None:
    for pattern in patterns:
        match = re.search(pattern, text, re.I | re.S)
        if match:
            return match
    return None


def _company_scope(text: str, security: MorningstarSecurity) -> str | None:
    """从多标的文章中只截取以公司名开头的 Markdown 小节。"""
    key = re.escape(security.company_name.lower().split()[0])
    heading = re.compile(rf"^(?P<marks>#{{2,6}})[^\n]*\b{key}[^\n]*$", re.I | re.M)
    for match in heading.finditer(text):
        next_heading = re.search(r"^#{2,6}\s", text[match.end() :], re.M)
        end = match.end() + next_heading.start() if next_heading else len(text)
        section = text[match.start() : end]
        if "fair value" in section.lower():
            return section
    return None


def _extract_value(text: str, security: MorningstarSecurity) -> tuple[float, str]:
    title_match = re.search(r"^Title:\s*([^\n]+)", text, re.I | re.M)
    title = title_match.group(1).lower() if title_match else ""
    dedicated = security.company_name.lower().split()[0] in title
    scoped = None if dedicated else _company_scope(text, security)
    search_text = scoped or text
    if security.ticker == "BRK.B":
        patterns = (
            r"\$[\d,]+\s*\(\$([\d,]+(?:\.\d+)?)\)\s*per class a \(b\)",
            r"class b[^\n]{0,80}?fair value estimate[^\d$]{0,30}\$?([\d,]+(?:\.\d+)?)",
            r"fair value estimate class b[^\d$]{0,20}\$?([\d,]+(?:\.\d+)?)",
        )
    elif security.ticker == "9992.HK":
        patterns = (
            r"morningstar.{0,120}?fair value estimate.{0,40}?hk\$\s*([\d,]+(?:\.\d+)?)",
            r"fair value estimate[^\n]{0,40}?(?:hkd|hk\$)\s*([\d,]+(?:\.\d+)?)",
        )
    else:
        patterns = (
            r"fair value estimate[^\n\d]{0,220}?(?:hkd|usd|hk\$|us\$|\$)?\s*([\d,]+(?:\.\d+)?)",
            r"fair value estimate(?: for [^\n]{0,40})? (?:at|to|of)\s*(?:hkd|usd|hk\$|us\$|\$)\s*([\d,]+(?:\.\d+)?)",
        )
    match = _first_match(patterns, search_text)
    if match is None:
        raise ValueError("页面未找到可归属于该标的的 Morningstar 公允价值")
    value = float(match.group(1).replace(",", ""))
    if not math.isfinite(value) or value <= 0:
        raise ValueError("Morningstar 公允价值必须是正有限数")
    context = search_text[max(0, match.start() - 120) : match.end() + 120]
    return value, _currency(context, security.currency)


def _published_date(text: str, fallback: datetime | None) -> str:
    match = re.search(r"Published Time:\s*([^\n]+)", text, re.I)
    if match:
        return _normalise_date(match.group(1))
    if fallback is not None:
        return fallback.astimezone(UTC).date().isoformat()
    raise ValueError("来源页面缺发布日期")


def _validate_identity(text: str, security: MorningstarSecurity, source_url: str) -> None:
    lowered = text.lower()
    if security.company_name.lower().split()[0] not in lowered:
        raise ValueError("来源页面与标的公司不匹配")
    if "morningstar" not in lowered:
        raise ValueError("来源页面未明确标注 Morningstar")
    title_match = re.search(r"^Title:\s*([^\n]+)", text, re.I | re.M)
    title = title_match.group(1).lower() if title_match else lowered[:300]
    company_key = security.company_name.lower().split()[0]
    dedicated = (
        company_key in title
        or f"key morningstar metrics for {company_key}" in lowered
        or _company_scope(text, security) is not None
    )
    if not dedicated:
        raise ValueError("多标的页面无法安全归属公允价值")
    host = (urlparse(source_url).hostname or "").lower()
    if not (
        host == "morningstar.com"
        or host.endswith(".morningstar.com")
        or host in {"theedgemalaysia.com", "www.theedgesingapore.com"}
    ):
        raise ValueError("来源域名不在核准名单")


class MorningstarPublicProvider:
    """公开无登录 provider；不接触账户、交易或付费接口。"""

    def __init__(
        self,
        *,
        timeout: float = 35.0,
        session: requests.Session | None = None,
        reader_min_interval: float = 2.5,
    ) -> None:
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", _USER_AGENT)
        self.reader_min_interval = max(0.0, reader_min_interval)
        self._last_reader_request_at: float | None = None
        self._memo: tuple[
            datetime,
            dict[str, MorningstarFairValue],
            dict[str, str],
        ] | None = None

    def _reader_get(self, url: str) -> requests.Response:
        """节流并重试公共文本镜像，避免一封邮件的双读触发临时限流。"""
        response: requests.Response | None = None
        for attempt in range(4):
            if self._last_reader_request_at is not None:
                elapsed = time.monotonic() - self._last_reader_request_at
                if elapsed < self.reader_min_interval:
                    time.sleep(self.reader_min_interval - elapsed)
            response = self.session.get(url, timeout=self.timeout)
            self._last_reader_request_at = time.monotonic()
            if response.status_code not in {429, 500, 502, 503, 504}:
                return response
            if attempt < 3:
                retry_after = getattr(response, "headers", {}).get("Retry-After")
                try:
                    wait = float(retry_after) if retry_after else 3.0 * (2**attempt)
                except (TypeError, ValueError):
                    wait = 3.0 * (2**attempt)
                time.sleep(min(max(wait, 1.0), 30.0))
        assert response is not None
        return response

    def _discover(self, security: MorningstarSecurity) -> list[_Candidate]:
        response = self.session.get(
            _GOOGLE_NEWS,
            params={
                "q": security.news_query,
                "hl": "en-US",
                "gl": "US",
                "ceid": "US:en",
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "xml")
        candidates = [
            _Candidate(
                url,
                (
                    datetime.fromisoformat(security.curated_dates[index]).replace(tzinfo=UTC)
                    if index < len(security.curated_dates)
                    else None
                ),
            )
            for index, url in enumerate(security.curated_urls)
        ]
        latest_curated = max(
            (candidate.published_at for candidate in candidates if candidate.published_at),
            default=None,
        )
        decoder = importlib.import_module("googlenewsdecoder")
        for item in soup.find_all("item")[:10]:
            source_node = item.find("source")
            source = source_node.get_text(" ", strip=True) if source_node else ""
            if "morningstar" not in source.lower() and security.ticker != "9992.HK":
                continue
            published = (
                parsedate_to_datetime(item.pubDate.get_text(strip=True)) if item.pubDate else None
            )
            if (
                latest_curated is not None
                and published is not None
                and published <= latest_curated
            ):
                continue
            decoded = decoder.new_decoderv1(item.link.get_text(strip=True))
            if not isinstance(decoded, Mapping) or not decoded.get("status"):
                continue
            url = str(decoded.get("decoded_url") or "")
            candidates.append(_Candidate(url, published))
        unique = {candidate.url: candidate for candidate in candidates if candidate.url}
        return sorted(
            unique.values(),
            key=lambda item: item.published_at or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )

    def _read(self, candidate: _Candidate, security: MorningstarSecurity) -> MorningstarFairValue:
        def parse(text: str, provider: str) -> MorningstarFairValue:
            _validate_identity(text, security, candidate.url)
            value, currency = _extract_value(text, security)
            return MorningstarFairValue(
                ticker=security.ticker,
                provider_code=security.provider_code,
                fair_value=value,
                currency=currency,
                rating_type="published-research",
                fair_value_updated_at=_published_date(text, candidate.published_at),
                retrieved_at="",
                source_provider=provider,
                source_url=candidate.url,
                observation_count=1,
            )

        errors: list[str] = []
        direct = self.session.get(candidate.url, timeout=self.timeout)
        if direct.ok and direct.text.strip():
            try:
                return parse(
                    BeautifulSoup(direct.text, "lxml").get_text("\n", strip=True),
                    "Morningstar public research",
                )
            except ValueError as exc:
                errors.append(str(exc))

        # Morningstar 偶尔以 HTTP 200 返回只有壳层/付费墙的 HTML。此时直接页虽非
        # 网络错误，正文仍不可验证；继续读取同一核准来源的文本镜像，而不是误把
        # 壳层当作“最新值缺失”并回退到更旧文章。
        source = candidate.url.split("://", 1)[-1]
        reader = self._reader_get(f"{_JINA_READER}{source}")
        if reader.ok and reader.text.strip():
            try:
                return parse(
                    reader.text,
                    "Morningstar public research via Jina Reader",
                )
            except ValueError as exc:
                errors.append(str(exc))
        if not errors:
            reader.raise_for_status()
            direct.raise_for_status()
        raise ValueError("；".join(errors) or "来源正文不可读取")

    def fetch_all(
        self,
        securities: Mapping[str, MorningstarSecurity],
        *,
        checked_at: datetime,
    ) -> tuple[dict[str, MorningstarFairValue], dict[str, str]]:
        if self._memo is not None:
            memo_at, memo_values, memo_failures = self._memo
            if checked_at.astimezone(UTC) - memo_at <= timedelta(minutes=30):
                return dict(memo_values), dict(memo_failures)
        values: dict[str, MorningstarFairValue] = {}
        failures: dict[str, str] = {}
        retrieved = checked_at.astimezone(UTC).isoformat()
        for ticker, security in securities.items():
            errors: list[str] = []
            try:
                candidates = self._discover(security)
            except Exception as exc:  # noqa: BLE001
                candidates = [
                    _Candidate(
                        url,
                        (
                            datetime.fromisoformat(security.curated_dates[index]).replace(
                                tzinfo=UTC
                            )
                            if index < len(security.curated_dates)
                            else None
                        ),
                    )
                    for index, url in enumerate(security.curated_urls)
                ]
                errors.append(f"发现失败: {type(exc).__name__}")
            for candidate in candidates:
                try:
                    first = self._read(candidate, security)
                    second = self._read(candidate, security)
                    if (
                        first.fair_value != second.fair_value
                        or first.currency != second.currency
                        or first.fair_value_updated_at != second.fair_value_updated_at
                    ):
                        raise ValueError("同一来源双读不一致")
                    values[ticker] = replace(
                        first,
                        retrieved_at=retrieved,
                        observation_count=2,
                    )
                    break
                except Exception as exc:  # noqa: BLE001
                    host = urlparse(candidate.url).hostname or "unknown"
                    errors.append(f"{host}: {str(exc)[:120]}")
            if ticker not in values:
                failures[ticker] = "; ".join(errors[-3:]) or "没有可验证的公开 Morningstar 值"
                logger.warning(
                    "morningstar.unavailable ticker=%s reason=%s",
                    ticker,
                    failures[ticker],
                )
        self._memo = (checked_at.astimezone(UTC), dict(values), dict(failures))
        return values, failures


def _retrieved_at(snapshot: MorningstarFairValue) -> datetime:
    return datetime.fromisoformat(snapshot.retrieved_at.replace("Z", "+00:00")).astimezone(UTC)


def _snapshot_from_dict(raw: Mapping[str, Any]) -> MorningstarFairValue:
    snapshot = MorningstarFairValue(
        ticker=str(raw["ticker"]),
        provider_code=str(raw["provider_code"]),
        fair_value=float(raw["fair_value"]),
        currency=str(raw["currency"]),
        rating_type=str(raw["rating_type"]),
        fair_value_updated_at=_normalise_date(str(raw["fair_value_updated_at"])),
        retrieved_at=str(raw["retrieved_at"]),
        source_provider=str(raw["source_provider"]),
        source_url=str(raw["source_url"]),
        observation_count=int(raw.get("observation_count") or 2),
        fallback_used=bool(raw.get("fallback_used", False)),
        warning=str(raw["warning"]) if raw.get("warning") else None,
    )
    expected = SECURITIES.get(snapshot.ticker)
    if (
        expected is None
        or snapshot.provider_code != expected.provider_code
        or snapshot.currency != expected.currency
    ):
        raise ValueError("缓存标的或币种与固定映射不一致")
    if not math.isfinite(snapshot.fair_value) or snapshot.fair_value <= 0:
        raise ValueError("缓存公允价值无效")
    _retrieved_at(snapshot)
    return snapshot


def load_cache(path: Path) -> dict[str, MorningstarFairValue]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload.get("fair_values") if isinstance(payload, Mapping) else None
        if not isinstance(rows, list):
            return {}
        return {
            snapshot.ticker: snapshot
            for row in rows
            if isinstance(row, Mapping)
            for snapshot in [_snapshot_from_dict(row)]
        }
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        logger.warning("morningstar.cache_invalid reason=%s", str(exc)[:180])
        return {}


def _save_cache(path: Path, values: Mapping[str, MorningstarFairValue]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "1.0",
        "source": "Morningstar public research",
        "fair_values": [asdict(values[ticker]) for ticker in SECURITIES if ticker in values],
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _validate_live(
    current: MorningstarFairValue,
    *,
    previous: MorningstarFairValue | None,
    current_price: float | None,
) -> None:
    expected = SECURITIES[current.ticker]
    if current.provider_code != expected.provider_code or current.currency != expected.currency:
        raise ValueError("标的代码或币种与固定映射不一致")
    if current.observation_count < 2:
        raise ValueError("公允价值未经双读确认")
    if current_price is not None and current_price > 0:
        ratio = current.fair_value / current_price
        if ratio < 0.20 or ratio > 5:
            raise ValueError("公允价值与现价数量级异常")
    if previous is None:
        return
    if current.fair_value_updated_at < previous.fair_value_updated_at:
        raise ValueError("公允价值日期倒退")
    if current.fair_value_updated_at == previous.fair_value_updated_at and not math.isclose(
        current.fair_value, previous.fair_value, rel_tol=1e-9
    ):
        raise ValueError("相同估值日期却返回不同数值")


def refresh_fair_values(
    *,
    provider: MorningstarProvider,
    state_dir: Path,
    prices: Mapping[str, float | None],
    checked_at: datetime,
    max_cache_age: timedelta = _MAX_CACHE_AGE,
) -> tuple[dict[str, MorningstarFairValue], dict[str, str]]:
    """获取全部值；单只失败时仅允许 48 小时内最后已验证快照回退。"""
    cache_path = state_dir / _CACHE_NAME
    previous = load_cache(cache_path)
    try:
        live, failures = provider.fetch_all(SECURITIES, checked_at=checked_at)
    except Exception as exc:  # noqa: BLE001
        live = {}
        reason = f"{type(exc).__name__}: {str(exc)[:180]}"
        failures = {ticker: reason for ticker in SECURITIES}

    accepted: dict[str, MorningstarFairValue] = {}
    final_failures = dict(failures)
    for ticker in SECURITIES:
        candidate = live.get(ticker)
        old = previous.get(ticker)
        if candidate is not None:
            try:
                _validate_live(candidate, previous=old, current_price=prices.get(ticker))
                accepted[ticker] = candidate
                final_failures.pop(ticker, None)
                continue
            except ValueError as exc:
                final_failures[ticker] = str(exc)
        if old is not None and checked_at.astimezone(UTC) - _retrieved_at(old) <= max_cache_age:
            reason = final_failures.get(ticker, "主源本次未返回")
            accepted[ticker] = replace(
                old,
                fallback_used=True,
                warning=f"公开主源短时不可用，沿用最近已验证快照：{reason}",
            )
        else:
            final_failures.setdefault(ticker, "没有 48 小时内可验证的 Morningstar 快照")

    merged = dict(previous)
    merged.update({ticker: value for ticker, value in accepted.items() if not value.fallback_used})
    if merged:
        _save_cache(cache_path, merged)
    logger.info(
        "morningstar.prepared live=%d fallback=%d unavailable=%d checked_at=%s",
        sum(not value.fallback_used for value in accepted.values()),
        sum(value.fallback_used for value in accepted.values()),
        len(SECURITIES) - len(accepted),
        checked_at.isoformat(),
    )
    return accepted, final_failures
