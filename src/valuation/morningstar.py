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
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup

from src.utils.runtime_budget import RuntimeBudget

logger = logging.getLogger(__name__)

_CACHE_NAME = "morningstar_fair_values.json"
_MAX_CACHE_AGE = timedelta(hours=48)
_GOOGLE_NEWS = "https://news.google.com/rss/search"
_JINA_READER = "https://r.jina.ai/http://"
_USER_AGENT = "daily-market-brief/1.0 (+public-source-validation)"
_DEFAULT_SECONDARY = object()


@dataclass(frozen=True)
class MorningstarSecurity:
    ticker: str
    provider_code: str
    currency: str
    company_name: str
    news_query: str
    curated_urls: tuple[str, ...] = ()
    curated_dates: tuple[str, ...] = ()
    curated_values: tuple[float, ...] = ()
    listing_id: str | None = None


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
            "https://www.morningstar.com/company-reports/"
            "1494726-tencent-earnings-ai-spending-weighs-on-near-term-cash-flow-"
            "but-core-business-anchors-valuation?listing=0P00009S22",
        ),
        ("2026-08-12",),
        # Morningstar 2026-08-12 analyst note: HKD 780 per Hong Kong share.
        (780.0,),
        listing_id="0P00009S22",
    ),
    "9992.HK": MorningstarSecurity(
        "9992.HK",
        "XHKG:09992",
        "HKD",
        "Pop Mart",
        '"Pop Mart" Morningstar "fair value"',
        (
            "https://www.morningstar.com/company-reports/"
            "1496104-pop-mart-earnings-valuation-cut-by-20-as-weak-overseas-sales-"
            "drag-growth-shares-still-cheap?listing=0P0001L8KX",
        ),
        ("2026-08-21",),
        # Morningstar 2026-08-21 note cut the prior HKD 280 estimate by 20%.
        (224.0,),
        listing_id="0P0001L8KX",
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
    # Independent live fallback is not an old cached observation.
    stale_cache: bool = False


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
    known_value: float | None = None
    headline: str | None = None


_REPORT_LINK_RE = re.compile(
    r"^###\s+\[(?P<headline>[^\]]+)\]\((?P<url>https?://www\.morningstar\.com/company-reports/[^)]+)\)$",
    re.I,
)
_REPORT_DATE_RE = re.compile(r"\b([A-Z][a-z]{2} \d{1,2}, \d{4})$")


def _parse_company_report_candidates(text: str) -> list[_Candidate]:
    """解析 Morningstar 官方 company-reports 列表中的报告 URL 与发布日期。"""
    lines = text.splitlines()
    candidates: list[_Candidate] = []
    for index, line in enumerate(lines):
        link_match = _REPORT_LINK_RE.match(line.strip())
        if not link_match:
            continue
        published_at: datetime | None = None
        for following in lines[index + 1 : index + 14]:
            if following.strip().startswith("### "):
                break  # 不把下一篇报告的日期借给当前报告。
            date_match = _REPORT_DATE_RE.search(following.strip())
            if date_match:
                published_at = datetime.strptime(
                    date_match.group(1), "%b %d, %Y"
                ).replace(tzinfo=UTC)
                break
        if published_at is None:
            continue
        url = re.sub(r"^http://", "https://", link_match.group("url"), flags=re.I)
        candidates.append(_Candidate(url, published_at, headline=link_match.group("headline")))
    return candidates


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


def _extract_hk_headline_value(
    headline: str, security: MorningstarSecurity,
) -> tuple[float, str] | None:
    """仅接受本公司标题中明确以港币计价的公允价值，不用涨跌幅推算。"""
    if not security.ticker.endswith(".HK"):
        return None
    if security.company_name.lower().split()[0] not in headline.lower():
        return None
    amount = r"(?:HKD|HK\$)\s*(\d[\d,]*(?:\.\d+)?)\b"
    phrase = r"fair\s+value(?:\s+estimate)?"
    patterns = (
        rf"{amount}\s+{phrase}",
        rf"{phrase}\s*:?\s*(?:(?:is|remains|unchanged|maintained|raised|increased|cut|reduced)\s+)*"
        rf"(?:(?:at|to|of)\s+)?{amount}",
    )
    values = set()
    for pattern in patterns:
        for match in re.finditer(pattern, headline, re.I):
            if re.search(r"\b(?:previous|prior|old|former|from)\s*$", headline[:match.start()], re.I):
                continue
            values.add(float(match.group(1).replace(",", "")))
    if len(values) > 1:
        raise ValueError("报告标题存在多个不同公允价值，需核对正文")
    if not values:
        return None
    value = values.pop()
    if not math.isfinite(value) or value <= 0:
        raise ValueError("Morningstar 公允价值必须是正有限数")
    return value, "HKD"


def _research_text(html: str) -> str:
    """保留公开 HTML 的报告标题/日期，避免正文转文本时丢失关键字段。"""
    soup = BeautifulSoup(html, "lxml")
    title = soup.find("meta", property="og:title")
    heading = str(title.get("content", "")) if title else ""
    if not heading and soup.title:
        heading = soup.title.get_text(" ", strip=True)
    published = soup.find("meta", property="article:published_time")
    prefix = f"Title: {heading}\n" if heading else ""
    if published and published.get("content"):
        prefix += f"Published Time: {published['content']}\n"
    return prefix + soup.get_text("\n", strip=True)


def _extract_value(text: str, security: MorningstarSecurity) -> tuple[float, str]:
    title_match = re.search(r"^Title:\s*([^\n]+)", text, re.I | re.M)
    title = title_match.group(1).lower() if title_match else ""
    headline_value = _extract_hk_headline_value(title, security)
    if headline_value is not None:
        return headline_value
    dedicated = security.company_name.lower().split()[0] in title
    scoped = None if dedicated else _company_scope(text, security)
    search_text = scoped or text
    if security.ticker.endswith(".HK"):
        search_text = re.split(r"^## (?:Company Report Archive|Share This Report)\b", search_text, flags=re.M)[0]
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
        secondary_provider: MorningstarProvider | None | object = _DEFAULT_SECONDARY,
    ) -> None:
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", _USER_AGENT)
        self.reader_min_interval = max(0.0, reader_min_interval)
        if secondary_provider is _DEFAULT_SECONDARY:
            # 自定义 session 主要用于完全离线的单元测试；生产默认启用 Yahoo
            # 分发的 Morningstar 报告作为独立读取与回退渠道。
            if session is None:
                from src.valuation.yahoo_morningstar import YahooMorningstarProvider

                self.secondary_provider: MorningstarProvider | None = (
                    YahooMorningstarProvider(timeout=timeout)
                )
            else:
                self.secondary_provider = None
        else:
            self.secondary_provider = secondary_provider  # type: ignore[assignment]
        self._last_reader_request_at: float | None = None
        self._budget = RuntimeBudget()
        self._inflight_values: dict[str, MorningstarFairValue] = {}
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
        candidates = [
            _Candidate(
                url,
                (
                    datetime.fromisoformat(security.curated_dates[index]).replace(tzinfo=UTC)
                    if index < len(security.curated_dates)
                    else None
                ),
                (
                    security.curated_values[index]
                    if index < len(security.curated_values)
                    else None
                ),
            )
            for index, url in enumerate(security.curated_urls)
        ]
        if security.listing_id:
            listing_url = (
                f"{_JINA_READER}www.morningstar.com/company-reports"
                f"?listing={security.listing_id}"
            )
            try:
                listing = self._reader_get(listing_url)
                listing.raise_for_status()
                candidates.extend(_parse_company_report_candidates(listing.text))
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "morningstar.listing_discovery_failed ticker=%s reason=%s",
                    security.ticker,
                    str(exc)[:160],
                )

        latest_curated = max(
            (candidate.published_at for candidate in candidates if candidate.published_at),
            default=None,
        )
        try:
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
            decoder = importlib.import_module("googlenewsdecoder")
            for item in soup.find_all("item")[:10]:
                source_node = item.find("source")
                source = source_node.get_text(" ", strip=True) if source_node else ""
                if "morningstar" not in source.lower():
                    continue
                published = (
                    parsedate_to_datetime(item.pubDate.get_text(strip=True))
                    if item.pubDate
                    else None
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
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "morningstar.google_discovery_failed ticker=%s reason=%s",
                security.ticker,
                str(exc)[:160],
            )

        unique: dict[str, _Candidate] = {}
        for candidate in candidates:
            if not candidate.url:
                continue
            previous = unique.get(candidate.url)
            if previous is None or (
                previous.known_value is None and candidate.known_value is not None
            ):
                unique[candidate.url] = candidate
        return sorted(
            unique.values(),
            key=lambda item: item.published_at or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )

    def _read_listing(self, candidate: _Candidate, security: MorningstarSecurity) -> str:
        """同一官方报告的目录备用路径；不宣称是独立分发源。"""
        if not security.listing_id:
            raise ValueError("该标的没有官方报告目录")
        if parse_qs(urlparse(candidate.url).query).get("listing") != [security.listing_id]:
            raise ValueError("报告上市口径与官方目录不匹配")
        listing = self._reader_get(
            f"{_JINA_READER}www.morningstar.com/company-reports?listing={security.listing_id}"
        )
        listing.raise_for_status()
        for entry in _parse_company_report_candidates(listing.text):
            if entry.url != candidate.url or entry.published_at != candidate.published_at:
                continue
            if not entry.headline or _extract_hk_headline_value(entry.headline, security) is None:
                raise ValueError("官方目录标题未明确给出本标的港币公允价值")
            return (
                f"Title: {entry.headline}\nPublished Time: {entry.published_at.isoformat()}\n"
                "Morningstar official company report listing"
            )
        raise ValueError("官方目录未找到同一报告及发布日期")

    def _read(self, candidate: _Candidate, security: MorningstarSecurity) -> MorningstarFairValue:
        def parse(text: str, provider: str) -> MorningstarFairValue:
            _validate_identity(text, security, candidate.url)
            headline = re.search(r"^Title:\s*([^\n]+)", text, re.I | re.M)
            explicit = _extract_hk_headline_value(headline.group(1), security) if headline else None
            if explicit is not None:
                value, currency = explicit
            elif candidate.known_value is not None:
                value = candidate.known_value
                currency = security.currency
            else:
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
        try:
            direct = self.session.get(candidate.url, timeout=self.timeout)
            if direct.ok and direct.text.strip():
                return parse(
                    _research_text(direct.text),
                    "Morningstar public research",
                )
            errors.append(f"直接页 HTTP {direct.status_code}")
        except (requests.RequestException, ValueError) as exc:
            errors.append(str(exc))

        # Morningstar 偶尔以 HTTP 200 返回只有壳层/付费墙的 HTML。此时直接页虽非
        # 网络错误，正文仍不可验证；继续读取同一核准来源的文本镜像，而不是误把
        # 壳层当作“最新值缺失”并回退到更旧文章。
        source = candidate.url.split("://", 1)[-1]
        try:
            reader = self._reader_get(f"{_JINA_READER}{source}")
            if reader.ok and reader.text.strip():
                return parse(
                    reader.text,
                    "Morningstar public research via Jina Reader",
                )
            errors.append(f"正文镜像 HTTP {reader.status_code}")
        except (requests.RequestException, ValueError) as exc:
            errors.append(str(exc))
        if security.listing_id:
            try:
                return parse(
                    self._read_listing(candidate, security),
                    "Morningstar official report listing via Jina Reader",
                )
            except (requests.RequestException, ValueError) as exc:
                errors.append(str(exc))
        raise ValueError("；".join(errors) or "来源正文不可读取")

    def fetch_all(
        self,
        securities: Mapping[str, MorningstarSecurity],
        *,
        checked_at: datetime,
    ) -> tuple[dict[str, MorningstarFairValue], dict[str, str]]:
        self._inflight_values = {}
        return self._budget.run(
            "morningstar", lambda: self._fetch_all(securities, checked_at=checked_at),
            seconds=330,
            fallback=lambda: (dict(self._inflight_values), {
                ticker: "公开估值取数超时，已保留完成项并检查有效快照"
                for ticker in securities if ticker not in self._inflight_values
            }),
        )

    def _fetch_all(
        self, securities: Mapping[str, MorningstarSecurity], *, checked_at: datetime,
    ) -> tuple[dict[str, MorningstarFairValue], dict[str, str]]:
        values: dict[str, MorningstarFairValue] = {}
        memo_at = checked_at.astimezone(UTC)
        if self._memo is not None:
            memo_at, memo_values, _memo_failures = self._memo
            # Reuse only within the exact same observation request. A final check
            # at a later time must discover/read again, even if values are unchanged.
            if checked_at.astimezone(UTC) == memo_at:
                values = {ticker: value for ticker, value in memo_values.items() if ticker in securities}
                if set(values) == set(securities):
                    return values, {}
            else:
                memo_at = checked_at.astimezone(UTC)
        pending = {ticker: security for ticker, security in securities.items() if ticker not in values}
        # Only publish completed, reconciled observations to the timeout fallback.
        # A signal can interrupt between the primary read and secondary validation.
        self._inflight_values = dict(values)
        failures: dict[str, str] = {}
        secondary_values: dict[str, MorningstarFairValue] = {}
        secondary_failures: dict[str, str] = {}
        if self.secondary_provider is not None:
            try:
                secondary_values, secondary_failures = self.secondary_provider.fetch_all(
                    pending, checked_at=checked_at
                )
            except Exception as exc:  # noqa: BLE001
                secondary_failures = {
                    ticker: f"独立备源整体失败: {type(exc).__name__}"
                    for ticker in pending
                }
        retrieved = checked_at.astimezone(UTC).isoformat()
        for ticker, security in pending.items():
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
                        (
                            security.curated_values[index]
                            if index < len(security.curated_values)
                            else None
                        ),
                    )
                    for index, url in enumerate(security.curated_urls)
                ]
                errors.append(f"发现失败: {type(exc).__name__}")
            for candidate_index, candidate in enumerate(candidates):
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
                    error_text = str(exc)
                    errors.append(f"{host}: {error_text[:120]}")
                    logger.info("morningstar.candidate_failed ticker=%s url=%s reason=%s", ticker, candidate.url, error_text[:240])
                    # 港股的公开研究页会对公允价值字段做前端混淆。若最新候选无法
                    # 验证，宁可触发 48 小时快照回退/待更新，也不能继续采用更旧
                    # 文章并把它误标成当天最新值。
                    irrelevant = any(
                        marker in error_text
                        for marker in (
                            "来源页面与标的公司不匹配",
                            "多标的页面无法安全归属公允价值",
                        )
                    )
                    if (
                        security.ticker.endswith(".HK")
                        and candidate_index == 0
                        and not irrelevant
                    ):
                        break
            if ticker not in values:
                failures[ticker] = "; ".join(errors[-3:]) or "没有可验证的公开 Morningstar 值"
                logger.warning(
                    "morningstar.unavailable ticker=%s reason=%s",
                    ticker,
                    failures[ticker],
                )
            secondary = secondary_values.get(ticker)
            primary = values.get(ticker)
            if primary is None and secondary is not None:
                values[ticker] = replace(
                    secondary,
                    fallback_used=True,
                    warning="Morningstar 官方公开页不可读，采用 Yahoo 分发的最新 Morningstar 报告",
                )
                failures.pop(ticker, None)
            elif primary is not None and secondary is not None:
                try:
                    values[ticker] = _reconcile_independent_sources(primary, secondary)
                    failures.pop(ticker, None)
                except ValueError as exc:
                    values.pop(ticker, None)
                    failures[ticker] = str(exc)
            elif primary is None and ticker in secondary_failures:
                failures[ticker] = (
                    f"{failures.get(ticker, 'Morningstar 官方公开页不可用')}；"
                    f"Yahoo 备源: {secondary_failures[ticker]}"
                )[:500]
            if ticker in values:
                self._inflight_values[ticker] = values[ticker]
        # 只在同一观测请求内复用；发送前的晚时点复核会重读所有标的。
        self._memo = (memo_at, dict(values), dict(failures))
        return values, failures


def _reconcile_independent_sources(
    primary: MorningstarFairValue,
    secondary: MorningstarFairValue,
) -> MorningstarFairValue:
    """日期不同时取较新报告；同日冲突按来源层级取官方主源。"""
    if primary.ticker != secondary.ticker or primary.currency != secondary.currency:
        raise ValueError("Morningstar 主备源标的或币种不一致")
    same_value = math.isclose(primary.fair_value, secondary.fair_value, rel_tol=1e-9)
    if primary.fair_value_updated_at == secondary.fair_value_updated_at and not same_value:
        return replace(
            primary,
            observation_count=primary.observation_count + secondary.observation_count,
            warning=(
                "Morningstar 主备源同日冲突；按来源层级采用官方主源 "
                f"{primary.fair_value:g} {primary.currency}，Yahoo 备源为 "
                f"{secondary.fair_value:g} {secondary.currency}"
            ),
        )
    if secondary.fair_value_updated_at > primary.fair_value_updated_at:
        chosen, older = secondary, primary
    else:
        chosen, older = primary, secondary
    if same_value:
        warning = "独立分发报告已复核；新报告可能继续维持原公允价值"
    else:
        warning = (
            f"采用较新报告值；较旧来源为 {older.fair_value:g} {older.currency}"
        )
    return replace(
        chosen,
        observation_count=primary.observation_count + secondary.observation_count,
        warning=warning,
    )


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
        stale_cache=bool(raw.get("stale_cache", False)),
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
        valid = {}
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            try:
                snapshot = _snapshot_from_dict(row)
                valid[snapshot.ticker] = snapshot
            except (KeyError, TypeError, ValueError):
                logger.warning("morningstar.cache_row_invalid skipped=true")
        return valid
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
        if old is not None and timedelta(0) <= checked_at.astimezone(UTC) - _retrieved_at(old) <= max_cache_age:
            reason = final_failures.get(ticker, "主源本次未返回")
            accepted[ticker] = replace(
                old,
                fallback_used=True,
                stale_cache=True,
                warning=f"公开主源短时不可用，沿用最近已验证快照：{reason}",
            )
        else:
            final_failures.setdefault(ticker, "没有 48 小时内可验证的 Morningstar 快照")

    merged = dict(previous)
    merged.update({ticker: value for ticker, value in accepted.items() if not value.stale_cache})
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
