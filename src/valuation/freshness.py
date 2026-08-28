"""官方财报发现与新鲜度闸门。

美国上市公司直接查询 SEC submissions JSON。普通 8-K 不触发估值更新，只有
Item 2.02（经营结果及财务状况）与 10-Q/10-K 才属于国内发行人的财务底稿。
TSM 属外国私人发行人，20-F 与 6-K 都进入复核；6-K 是否改变估值由下一层解析器判断。
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from src.collectors.buffett_13f import SEC_UA
from src.utils.retry import retry
from src.utils.secrets import redact_secrets
from src.valuation.models import FreshnessResult, OfficialDocument
from src.valuation.policy import ValuationPolicy

logger = logging.getLogger(__name__)

_SEC_SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
_HKEX_SEARCH = (
    "https://www1.hkexnews.hk/search/titlesearch.xhtml"
    "?category=0&lang=EN&market=SEHK&stockId={stock_id}"
)
_HKEX_RESULT_RE = re.compile(
    r"(?:RESULTS?\s+ANNOUNCEMENT|ANNOUNCEMENT\s+OF\s+THE\s+RESULTS|BUSINESS\s+UPDATE)",
    re.IGNORECASE,
)
_HKEX_PERIOD_RE = re.compile(
    r"ENDED\s+(\d{1,2}\s+[A-Z]+\s+\d{4})",
    re.IGNORECASE,
)


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _row(recent: Mapping[str, Any], index: int, key: str) -> str:
    values = recent.get(key) or []
    if index >= len(values):
        return ""
    return str(values[index] or "").strip()


def _is_relevant_filing(*, form: str, items: str, foreign_issuer: bool) -> bool:
    normalized = form.upper().strip()
    if foreign_issuer:
        return normalized in {"20-F", "20-F/A", "6-K", "6-K/A"}
    if normalized in {"10-K", "10-K/A", "10-Q", "10-Q/A"}:
        return True
    if normalized in {"8-K", "8-K/A"}:
        return "2.02" in {part.strip() for part in items.split(",")}
    return False


def latest_relevant_sec_document(
    *,
    ticker: str,
    cik: str,
    recent: Mapping[str, Any],
    discovered_at: datetime,
) -> OfficialDocument | None:
    """从 SEC recent 并行数组中选择最新估值相关文件（纯函数，便于测试）。"""
    forms = recent.get("form") or []
    foreign_issuer = ticker == "TSM"
    for index, raw_form in enumerate(forms):
        form = str(raw_form or "").strip()
        items = _row(recent, index, "items")
        if not _is_relevant_filing(form=form, items=items, foreign_issuer=foreign_issuer):
            continue
        accession = _row(recent, index, "accessionNumber")
        accepted = _row(recent, index, "acceptanceDateTime")
        primary_document = _row(recent, index, "primaryDocument")
        if not accession or not accepted or not primary_document:
            continue
        cik_plain = cik.lstrip("0") or "0"
        accession_plain = accession.replace("-", "")
        url = (
            "https://www.sec.gov/Archives/edgar/data/"
            f"{cik_plain}/{accession_plain}/{primary_document}"
        )
        return OfficialDocument(
            document_id=accession,
            document_type=form,
            report_period=_row(recent, index, "reportDate") or None,
            published_at=_parse_utc(accepted),
            discovered_at=discovered_at,
            source_url=url,
            source_domain="sec.gov",
            title=_row(recent, index, "primaryDocDescription") or form,
        )
    return None


@retry(max_attempts=3, base_delay=1.0)
def fetch_latest_sec_document(
    policy: ValuationPolicy,
    *,
    checked_at: datetime | None = None,
    session: requests.Session | None = None,
    download_original: bool = True,
) -> OfficialDocument | None:
    if not policy.sec_cik:
        return None
    if checked_at is None:
        checked_at = datetime.now(UTC)
    client = session or requests.Session()
    response = client.get(
        _SEC_SUBMISSIONS.format(cik=policy.sec_cik),
        headers={
            "User-Agent": SEC_UA,
            "Accept": "application/json",
            "Accept-Encoding": "gzip, deflate",
        },
        timeout=20,
    )
    response.raise_for_status()
    recent = ((response.json() or {}).get("filings") or {}).get("recent") or {}
    document = latest_relevant_sec_document(
        ticker=policy.ticker,
        cik=policy.sec_cik,
        recent=recent,
        discovered_at=checked_at,
    )
    if document is None or not download_original:
        return document
    original = client.get(
        document.source_url,
        headers={
            "User-Agent": SEC_UA,
            "Accept-Encoding": "gzip, deflate",
        },
        timeout=30,
    )
    original.raise_for_status()
    if not original.content:
        raise RuntimeError("SEC 官方原文为空")
    return replace(
        document,
        content_hash=hashlib.sha256(original.content).hexdigest(),
    )


def _hkex_report_period(title: str) -> str | None:
    match = _HKEX_PERIOD_RE.search(title)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1).title(), "%d %B %Y").date().isoformat()
    except ValueError:
        return None


def latest_relevant_hkex_document(
    *,
    ticker: str,
    html: str,
    discovered_at: datetime,
) -> OfficialDocument | None:
    """解析 HKEX 官方标题搜索结果；只接受业绩公告与泡泡玛特业务更新。"""
    soup = BeautifulSoup(html, "html.parser")
    for row in soup.select("tr"):
        link = row.select_one("div.doc-link a[href]")
        release_cell = row.select_one("td.release-time")
        headline = row.select_one("div.headline")
        if link is None or release_cell is None:
            continue
        title = " ".join(link.get_text(" ", strip=True).split())
        category = " ".join(headline.get_text(" ", strip=True).split()) if headline else ""
        is_results_category = any(
            marker in category.lower()
            for marker in ("interim results", "quarterly results", "final results")
        )
        is_popmart_update = ticker == "9992.HK" and "business update" in title.lower()
        if not (is_results_category or is_popmart_update) or not _HKEX_RESULT_RE.search(title):
            continue
        release_text = release_cell.get_text(" ", strip=True).replace("Release Time:", "").strip()
        try:
            local_time = datetime.strptime(release_text, "%d/%m/%Y %H:%M").replace(
                tzinfo=ZoneInfo("Asia/Hong_Kong")
            )
        except ValueError:
            continue
        url = urljoin("https://www1.hkexnews.hk", str(link.get("href")))
        document_id = Path(url).stem
        return OfficialDocument(
            document_id=document_id,
            document_type="HKEX_RESULTS",
            report_period=_hkex_report_period(title),
            published_at=local_time.astimezone(UTC),
            discovered_at=discovered_at,
            source_url=url,
            source_domain="hkexnews.hk",
            title=title,
        )
    return None


@retry(max_attempts=3, base_delay=1.0)
def fetch_latest_hkex_document(
    policy: ValuationPolicy,
    *,
    checked_at: datetime | None = None,
    session: requests.Session | None = None,
    download_original: bool = True,
) -> OfficialDocument | None:
    if not policy.hkex_stock_id:
        return None
    if checked_at is None:
        checked_at = datetime.now(UTC)
    client = session or requests.Session()
    response = client.get(
        _HKEX_SEARCH.format(stock_id=policy.hkex_stock_id),
        headers={"User-Agent": "Mozilla/5.0 daily-market-brief/0.1"},
        timeout=20,
    )
    response.raise_for_status()
    document = latest_relevant_hkex_document(
        ticker=policy.ticker,
        html=response.text,
        discovered_at=checked_at,
    )
    if document is None or not download_original:
        return document
    original = client.get(
        document.source_url,
        headers={"User-Agent": "Mozilla/5.0 daily-market-brief/0.1"},
        timeout=30,
    )
    original.raise_for_status()
    content_type = original.headers.get("Content-Type", "").lower()
    if "pdf" not in content_type and not original.content.startswith(b"%PDF"):
        raise RuntimeError("HKEX 原始业绩文件不是 PDF")
    return replace(
        document,
        content_hash=hashlib.sha256(original.content).hexdigest(),
    )


def evaluate_freshness(
    *,
    policy: ValuationPolicy,
    latest_document: OfficialDocument | None,
    valuation_document_id: str | None,
    checked_at: datetime,
    source_error: str | None = None,
) -> FreshnessResult:
    """把“是否最新”收敛成可阻断渲染的确定状态。"""
    if source_error:
        return FreshnessResult(
            ticker=policy.ticker,
            status="source_unavailable",
            checked_at=checked_at,
            valuation_document_id=valuation_document_id,
            reason=source_error,
        )
    if latest_document is None:
        return FreshnessResult(
            ticker=policy.ticker,
            status="source_unavailable",
            checked_at=checked_at,
            valuation_document_id=valuation_document_id,
            reason="官方源未返回可识别的最新财务文件",
        )
    if not valuation_document_id:
        return FreshnessResult(
            ticker=policy.ticker,
            status="new_filing_pending",
            checked_at=checked_at,
            latest_document=latest_document,
            reason="尚无经过批准的估值底稿",
        )
    if latest_document.document_id != valuation_document_id:
        return FreshnessResult(
            ticker=policy.ticker,
            status="new_filing_pending",
            checked_at=checked_at,
            latest_document=latest_document,
            valuation_document_id=valuation_document_id,
            reason=(
                f"官方最新文件 {latest_document.document_id} "
                f"晚于估值底稿 {valuation_document_id}"
            ),
        )
    return FreshnessResult(
        ticker=policy.ticker,
        status="current",
        checked_at=checked_at,
        latest_document=latest_document,
        valuation_document_id=valuation_document_id,
    )


def check_official_freshness(
    policies: Iterable[ValuationPolicy],
    *,
    valuation_document_ids: Mapping[str, str | None],
    checked_at: datetime | None = None,
    download_original: bool = True,
    prior_results: Mapping[str, FreshnessResult] | None = None,
) -> dict[str, FreshnessResult]:
    """检查所有官方源；单个发行人失败不会阻断其他持仓。"""
    if checked_at is None:
        checked_at = datetime.now(UTC)
    session = requests.Session()
    results: dict[str, FreshnessResult] = {}
    for policy in policies:
        accepted = valuation_document_ids.get(policy.ticker)
        try:
            if policy.sec_cik:
                latest = fetch_latest_sec_document(
                    policy,
                    checked_at=checked_at,
                    session=session,
                    download_original=download_original,
                )
            elif policy.hkex_stock_id:
                latest = fetch_latest_hkex_document(
                    policy,
                    checked_at=checked_at,
                    session=session,
                    download_original=download_original,
                )
            else:
                latest = None
            if (
                latest is not None
                and latest.content_hash is None
                and prior_results is not None
            ):
                prior_document = getattr(prior_results.get(policy.ticker), "latest_document", None)
                if (
                    prior_document is not None
                    and prior_document.document_id == latest.document_id
                    and prior_document.content_hash
                ):
                    latest = replace(latest, content_hash=prior_document.content_hash)
            results[policy.ticker] = evaluate_freshness(
                policy=policy,
                latest_document=latest,
                valuation_document_id=accepted,
                checked_at=checked_at,
            )
        except Exception as exc:  # noqa: BLE001
            message = f"{type(exc).__name__}: {redact_secrets(str(exc))[:180]}"
            logger.warning(
                "valuation.freshness_failed ticker=%s reason=%s",
                policy.ticker,
                message,
            )
            results[policy.ticker] = evaluate_freshness(
                policy=policy,
                latest_document=None,
                valuation_document_id=accepted,
                checked_at=checked_at,
                source_error=message,
            )
    return results
