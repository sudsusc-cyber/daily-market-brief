"""Pop Mart's explicitly approved Morgan Stanley target-price exception.

This is NOT Morningstar fair value or a DCF. Public articles are read directly;
search results are discovery only. No LLM, credentials or subscription is used.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, quote, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from src.valuation.models import ValuationDisplay

logger = logging.getLogger(__name__)
TICKER = "9992.HK"
STATE_NAME = "pop_mart_analyst_target.json"
BACKUP_NAME = "pop_mart_analyst_target_backup.json"
RECOVERY_NAME = "pop_mart_analyst_recovery.json"
BASELINE_NAME = "pop_mart_analyst_target.json"
HK = ZoneInfo("Asia/Hong_Kong")
_HOSTS = {"finance.sina.com.cn", "secure.aastocks.com", "m.moneydj.com", "www.etnet.com.hk"}
_MOBILE = "https://secure.aastocks.com/tc/mobile/News.aspx?NewsID={}&NewsSource=HK6"
_MONEYDJ = "https://m.moneydj.com/f1a.aspx?a=4498ccbb-7bcc-449f-ad17-e94ac1404692"
_ETNET = "https://www.etnet.com.hk/www/tc/stocks/realtime/quote_news_detail.php?newsid=20260818992&section=research&code=9992"
_SEED_ARTICLES = (
    _MOBILE.format("NOW.1539842"),
    "https://finance.sina.com.cn/stock/usstock/c/2026-08-21/doc-ininztwt6444665.shtml",
    _MONEYDJ,
    _ETNET,
)
_DISCOVERY = (
    "https://www.aastocks.com/tc/stocks/analysis/stock-aafn/09992/0/hk-stock-news/1",
    "https://stock.finance.sina.com.cn/hkstock/quotes/09992.html",
    "https://m.moneydj.com/indexPart/search_news.aspx?k=" + quote("泡泡瑪特"),
    "https://www.etnet.com.hk/www/tc/stocks/realtime/quote_news_list.php?section=research&code=9992",
)
_POP = re.compile(r"泡泡[玛瑪]特")
_BROKER = re.compile(r"摩根士丹利|大摩|Morgan Stanley", re.I)
_OTHER_BROKER = re.compile(r"摩根大通|摩通|小摩|高盛|美銀|美银|滙豐|匯豐|汇丰|瑞銀|瑞银|富瑞|花旗|野村|交銀|交银")
_TRANS = str.maketrans("標價從調維將為於幣給瑪", "标价从调维将为于币给玛")


class NoMatchingTargetError(ValueError):
    """A complete article discusses other brokers, not the chosen estimator."""


@dataclass(frozen=True)
class Candidate:
    url: str
    published_at: str | None = None


@dataclass(frozen=True)
class AnalystTarget:
    target_price: float
    published_at: str
    verified_at: str
    source_url: str
    evidence_sha256: str
    ticker: str = TICKER
    currency: str = "HKD"
    institution: str = "Morgan Stanley"
    value_type: str = "analyst_target_price"


def _date(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("source timestamp must include timezone")
    return result


def _allowed_article(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in _HOSTS or parsed.username:
        return False
    if parsed.hostname == "finance.sina.com.cn":
        return bool(re.search(r"/\d{4}-\d{2}-\d{2}/doc-[a-z0-9]+\.shtml$", parsed.path))
    query = parse_qs(parsed.query)
    if parsed.hostname == "secure.aastocks.com":
        return parsed.path == "/tc/mobile/News.aspx" and bool(re.fullmatch(r"NOW\.\d+", query.get("NewsID", [""])[0]))
    if parsed.hostname == "m.moneydj.com":
        return parsed.path == "/f1a.aspx" and bool(re.fullmatch(r"[a-f0-9-]{36}", query.get("a", [""])[0]))
    return (parsed.path == "/www/tc/stocks/realtime/quote_news_detail.php"
            and query.get("code") == ["9992"] and query.get("section") == ["research"]
            and bool(re.fullmatch(r"\d+", query.get("newsid", [""])[0])))


def _channel(url: str) -> str:
    host = urlparse(url).hostname or ""
    return {"secure.aastocks.com": "aastocks", "www.aastocks.com": "aastocks",
            "finance.sina.com.cn": "sina", "stock.finance.sina.com.cn": "sina",
            "m.moneydj.com": "moneydj", "www.etnet.com.hk": "etnet"}.get(host, "")


def _source_day(value: str) -> str:
    return _date(value).astimezone(HK).date().isoformat()


def _etnet_target(soup: BeautifulSoup) -> float:
    body = soup.select_one("#NewsContent")
    if body is None:
        raise ValueError("ETNet article body missing")
    table = body.find("table")
    if table is None:
        raise NoMatchingTargetError("not an ETNet broker-target table")
    rows = table.find_all("tr")
    if not rows:
        raise ValueError("empty ETNet target table")
    headers = [re.sub(r"\s+", "", cell.get_text()) for cell in rows[0].find_all(["td", "th"])]
    if len(headers) != 5 or "股份" not in headers[0] or "券商" not in headers[1] or "目標價" not in headers[2] or "元" not in headers[2]:
        raise ValueError("ETNet table columns changed")
    issuer, numbers = "", set()
    # Only the first/latest table. Later tables are explicitly historical.
    for row in rows[1:]:
        cells = row.find_all(["td", "th"], recursive=False)
        if len(cells) != 5 or any(cell.get("rowspan") or cell.get("colspan") for cell in cells):
            issuer = ""  # do not carry identity across a layout we cannot validate
            continue
        texts = [re.sub(r"\s+", "", cell.get_text()) for cell in cells]
        if texts[0]:
            issuer = texts[0]
        if not _POP.search(issuer) or not re.search(r"\(0?9992\)", issuer) or not _BROKER.fullmatch(texts[1]):
            continue
        target = texts[2].translate(_TRANS)
        match = re.fullmatch(r"(?:\d+(?:\.\d+)?→|维持|首予)?(\d+(?:\.\d+)?)", target)
        if not match:
            raise ValueError("ambiguous ETNet HKD target cell")
        numbers.add(float(match.group(1)))
    if not numbers:
        raise NoMatchingTargetError("ETNet latest table has no Morgan Stanley Pop Mart target")
    if len(numbers) != 1:
        raise ValueError("conflicting ETNet target rows")
    return next(iter(numbers))


def validate_target(value: AnalystTarget, *, checked_at: datetime) -> AnalystTarget:
    if (value.ticker, value.currency, value.institution, value.value_type) != (
        TICKER, "HKD", "Morgan Stanley", "analyst_target_price",
    ):
        raise ValueError("wrong security, currency, broker or valuation type")
    if isinstance(value.target_price, bool) or not math.isfinite(value.target_price) or not 0 < value.target_price < 10000:
        raise ValueError("invalid target price")
    if not _allowed_article(value.source_url) or not re.fullmatch(r"[a-f0-9]{64}", value.evidence_sha256):
        raise ValueError("missing original article evidence")
    if not _date(value.published_at) <= _date(value.verified_at) <= checked_at:
        raise ValueError("future or inconsistent source dates")
    return value


def _target_numbers(text: str, *, hkd_context: bool) -> set[float]:
    text = re.sub(r"\s+", "", text.translate(_TRANS))
    numbers: set[float] = set()
    # Always take the destination, never the old target, spot price or EPS.
    for match in re.finditer(r"目标价[^，。；;!?！？]{0,65}", text):
        phrase = match.group()
        targets = list(re.finditer(r"(?:至|为|予|在|维持)(\d+(?:\.\d+)?)(港元|港币|HKD|元)?", phrase, re.I))
        if not targets:
            targets = list(re.finditer(r"目标价(?:维持|不变)?(?:在)?(\d+(?:\.\d+)?)(港元|港币|HKD|元)", phrase, re.I))
        for target in targets[-1:]:
            tail = phrase[target.end(1):]
            if re.match(r"(?:美元|人民币|美金|%|倍)", tail):
                raise ValueError("target is not a HKD per-share price")
            unit = target.group(2)
            if unit in {"港元", "港币", "HKD"} or (unit == "元" and hkd_context):
                numbers.add(float(target.group(1)))
    return numbers


def parse_article(url: str, html: str, *, checked_at: datetime) -> AnalystTarget:
    if not _allowed_article(url):
        raise ValueError("unapproved article URL")
    soup = BeautifulSoup(html, "lxml")
    channel = _channel(url)
    if channel == "sina":
        title_node = soup.find("h1")
        body_node = soup.select_one("#artibody")
        published_node = soup.select_one('meta[property="article:published_time"]')
        if not title_node or not body_node or not published_node:
            raise ValueError("Sina article/date missing")
        published = _date(str(published_node.get("content", "")))
    elif channel == "aastocks":
        title_node = soup.select_one(".quote_table_header_text")
        body_node = soup.select_one("#lblContent")
        container = soup.select_one('[id$="pNewsContent"] .padding2')
        if not title_node or not body_node or not container:
            raise ValueError("AASTOCKS article/date missing")
        date_text = container.get_text(" ", strip=True).split(body_node.get_text(" ", strip=True))[0]
        match = re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", date_text)
        if not match:
            raise ValueError("AASTOCKS publication timestamp missing")
        published = datetime.fromisoformat(match.group()).replace(tzinfo=HK)
    elif channel == "moneydj":
        title_node = soup.select_one("h1#NewsHD")
        body_node = soup.select_one("#f1a_newsData")
        metadata = []
        for script in soup.select('script[type="application/ld+json"]'):
            try:
                item = json.loads(script.get_text())
                if isinstance(item, dict) and item.get("@type") == "NewsArticle":
                    metadata.append(item)
            except (ValueError, TypeError):
                continue
        if not title_node or not body_node or len(metadata) != 1:
            raise ValueError("MoneyDJ article/date missing")
        published = _date(str(metadata[0].get("datePublished", "")))
        if metadata[0].get("headline") != title_node.get_text(strip=True):
            raise ValueError("MoneyDJ headline metadata mismatch")
    else:
        title_node = soup.select_one("h1.ArticleHdr")
        body_node = soup.select_one("#NewsContent")
        date_node = soup.select_one('.DivArticleList[itemtype="https://schema.org/Article"] .date')
        if not title_node or not body_node or not date_node:
            raise ValueError("ETNet article/date missing")
        published = datetime.strptime(date_node.get_text(strip=True), "%d/%m/%Y %H:%M").replace(tzinfo=HK)
    title = title_node.get_text(" ", strip=True)
    body = body_node.get_text(" ", strip=True)
    if channel == "etnet" and body_node.find("table") is not None:
        target = _etnet_target(soup)
        return validate_target(AnalystTarget(
            target, published.isoformat(), checked_at.isoformat(), url,
            hashlib.sha256((title + "\n" + body).encode()).hexdigest(),
        ), checked_at=checked_at)
    if channel == "moneydj":
        if not _POP.search(title) or not re.search(r"目[标標][价價]", title):
            raise NoMatchingTargetError("MoneyDJ article is not a Pop Mart target update")
        # Multi-broker articles are common. Only a full paragraph explicitly
        # identifying BOTH Morgan Stanley and Pop Mart may supply the target.
        sections = [part for part in body_node.get_text("\n", strip=True).splitlines()
                    if _BROKER.search(part) and _POP.search(part) and not _OTHER_BROKER.search(part)]
        if not sections:
            raise NoMatchingTargetError("no unambiguous Morgan Stanley Pop Mart paragraph")
        body = "\n".join(sections)
    elif not _POP.search(title) or not _BROKER.search(title) or _OTHER_BROKER.search(title + body):
        raise ValueError("not an unambiguous Morgan Stanley Pop Mart report")
    if not re.search(r"目[标標][价價]", title) or not _BROKER.search(body) or not _POP.search(body):
        raise ValueError("headline/body security and broker do not match")
    if re.search(r"回[顾顧]|去年|上年|曾[给給]予", title) or re.search(r"(?:去年|上年).{0,10}目[标標][价價]", body):
        raise ValueError("retrospective target is not a new broker update")
    hkd_context = bool(re.search(r"0?9992(?:\.HK|\b)|港股|港元|港[币幣]", title + body))
    title_values = _target_numbers(title, hkd_context=hkd_context)
    body_values = _target_numbers(body, hkd_context=hkd_context)
    if len(body_values) != 1 or (title_values and title_values != body_values):
        raise ValueError("missing or conflicting absolute target in article body")
    return validate_target(AnalystTarget(
        target_price=next(iter(body_values)),
        published_at=published.isoformat(),
        verified_at=checked_at.isoformat(),
        source_url=url,
        evidence_sha256=hashlib.sha256((title + "\n" + body).encode()).hexdigest(),
    ), checked_at=checked_at)


def load_target(*, state_dir: Path, config_dir: Path, checked_at: datetime) -> AnalystTarget | None:
    values = []
    for path in (config_dir / BASELINE_NAME, state_dir / BACKUP_NAME, state_dir / STATE_NAME, state_dir / RECOVERY_NAME):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            rows = payload.get("observations", [])
            for row in rows:
                try:
                    if set(row) != {field.name for field in fields(AnalystTarget)}:
                        raise ValueError("incomplete broker-target provenance")
                    values.append(validate_target(AnalystTarget(**row), checked_at=checked_at))
                except (ValueError, TypeError, KeyError):
                    logger.warning("pop_mart.invalid_cached_observation path=%s", path)
        except FileNotFoundError:
            continue
        except (OSError, ValueError, TypeError, AttributeError):
            logger.warning("pop_mart.invalid_cache path=%s", path)
    # File precedence must not replace a newer verified report with an older one.
    # Same-day disagreements retain the already-verified value; publication time
    # can be a syndication time, not the broker's revision timestamp.
    values.sort(key=lambda row: _date(row.published_at))
    chosen = None
    for value in values:
        if (chosen is None
            or _date(value.published_at).astimezone(HK).date() > _date(chosen.published_at).astimezone(HK).date()
            or (value.target_price == chosen.target_price and _date(value.verified_at) > _date(chosen.verified_at))):
            chosen = value
    return chosen


def save_target(value: AnalystTarget, *, state_dir: Path) -> None:
    payload = json.dumps({"schema_version": 1, "observations": [asdict(value)]}, ensure_ascii=False, indent=2)
    for name in (STATE_NAME, BACKUP_NAME, RECOVERY_NAME):
        path = state_dir / name
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, path)
        except OSError as exc:
            logger.warning("pop_mart.cache_write_failed path=%s reason=%s", path, exc)


class PopMartTargetProvider:
    """Independent rolling discovery/read pipelines; one failure cannot starve peers."""

    def __init__(self, *, budget_seconds: float = 24):
        self.budget_seconds = budget_seconds
        self.diagnostic: dict = {}

    def _get(self, url: str, *, deadline: float) -> str:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Pop Mart source budget exhausted")
        with requests.get(url, timeout=(min(3, remaining), min(6, remaining)),
                          headers={"User-Agent": "Mozilla/5.0"}, stream=True, allow_redirects=False) as response:
            if response.status_code != 200:
                raise ValueError(f"source HTTP {response.status_code}")
            chunks, size = [], 0
            for chunk in response.iter_content(32768):
                size += len(chunk)
                if size > 2_000_000 or time.monotonic() >= deadline:
                    raise TimeoutError("source response exceeds size/time budget")
                chunks.append(chunk)
            raw = b"".join(chunks)
            charset = re.search(rb'<meta[^>]+charset\s*=\s*["\x27]?([\w-]+)', raw[:10000], re.I)
            encoding = charset.group(1).decode("ascii") if charset else "utf-8"
            return raw.decode(encoding, errors="replace")

    def _discover(self, url: str, *, deadline: float) -> list[Candidate]:
        html = self._get(url, deadline=deadline)
        channel = _channel(url)
        if channel == "moneydj":
            rows = json.loads(html)
            if not isinstance(rows, list):
                raise ValueError("MoneyDJ search response changed")
            found = []
            for row in rows[:50]:
                title = str(row.get("Title", ""))
                if not _POP.search(title) or not re.search(r"目[标標][价價]", title):
                    continue
                candidate = urljoin(url, "/" + str(row.get("Url", "")).lstrip("/"))
                if _allowed_article(candidate):
                    date = datetime.fromisoformat(str(row["Date"]))
                    date = date.replace(tzinfo=HK) if date.tzinfo is None else date
                    found.append(Candidate(candidate, date.isoformat()))
            return sorted(found, key=lambda row: row.published_at, reverse=True)[:8]
        soup = BeautifulSoup(html, "lxml")
        if channel == "etnet":
            rows = soup.select("div.DivArticleList.dotLine")
            if not rows:
                raise ValueError("ETNet research listing missing")
            found = []
            for row in rows:
                link = row.select_one('a[href*="quote_news_detail.php"][href*="newsid="]')
                date_node = row.select_one(".date")
                if not link or not date_node:
                    continue
                candidate = urljoin(url, str(link.get("href", "")))
                if _allowed_article(candidate):
                    date = datetime.strptime(date_node.get_text(strip=True), "%d/%m/%Y %H:%M").replace(tzinfo=HK)
                    found.append(Candidate(candidate, date.isoformat()))
            if not found:
                raise ValueError("ETNet research dates/links missing")
            return sorted(set(found), key=lambda row: row.published_at, reverse=True)
        found = []
        news_found = False
        for link in soup.find_all("a", href=True):
            title = str(link.get("title", "")) + " " + link.get_text(" ", strip=True)
            href = str(link["href"])
            if urlparse(url).hostname == "stock.finance.sina.com.cn":
                candidate = href if _allowed_article(href) else None
            else:
                match = re.search(r"/09992/AAFN/(NOW\.\d+)/", href)
                candidate = _MOBILE.format(match.group(1)) if match else None
            news_found |= bool(candidate and title.strip())
            if candidate and _POP.search(title) and _BROKER.search(title):
                date_match = re.search(r"/(\d{4}-\d{2}-\d{2})/", candidate)
                published = date_match.group(1) + "T00:00:00+08:00" if date_match else None
                found.append(Candidate(candidate, published))
        if not news_found:
            raise ValueError("stock news listing missing; cannot confirm discovery")
        return list(dict.fromkeys(found))[:6]

    def fetch(self, *, known: AnalystTarget | None, checked_at: datetime) -> tuple[list[AnalystTarget], bool]:
        deadline = time.monotonic() + self.budget_seconds
        lock = threading.Lock()
        accepting = True
        values: list[AnalystTarget] = []
        reports: dict[str, dict] = {}
        unresolved: list[Candidate] = []
        inflight: dict[str, list[Candidate]] = {}

        def channel_worker(discovery_url: str) -> None:
            channel = _channel(discovery_url)
            report = {"discovery_ok": False, "completed": False, "read": 0, "no_match": 0, "errors": []}
            try:
                discovered = self._discover(discovery_url, deadline=deadline)
                # Retain support for simple injected test providers.
                discovered = [Candidate(row) if isinstance(row, str) else row for row in discovered]
                report["discovery_ok"] = True
            except Exception as exc:  # noqa: BLE001
                discovered = []
                report["errors"].append(f"discovery: {str(exc)[:160]}")
            # Dated archives let the backup catch up after an outage without
            # re-reading every historical report at each scheduled run.
            relevant = [row for row in discovered if not known or not row.published_at
                        or _source_day(row.published_at) >= _source_day(known.published_at)]
            report["discovered"] = len(relevant)
            with lock:
                if accepting:
                    inflight[channel] = list(relevant)
            seeds = ([known.source_url] if known and _channel(known.source_url) == channel else [])
            seeds += [url for url in _SEED_ARTICLES if _channel(url) == channel]
            candidates = {row.url: row for row in relevant}
            for url in seeds:
                candidates.setdefault(url, Candidate(url))
            visited: set[str] = set()
            capped = False
            for index, candidate in enumerate(candidates.values()):
                if index >= 8 or time.monotonic() >= deadline:
                    capped = True
                    with lock:
                        if accepting:
                            unresolved.extend(row for row in relevant if row.url not in visited)
                    break
                visited.add(candidate.url)
                try:
                    first = parse_article(candidate.url, self._get(candidate.url, deadline=deadline), checked_at=checked_at)
                    second = parse_article(candidate.url, self._get(candidate.url, deadline=deadline), checked_at=checked_at)
                    if (first.target_price, first.published_at) != (second.target_price, second.published_at):
                        raise ValueError("article changed between verification reads")
                    if candidate.published_at and _source_day(candidate.published_at) != _source_day(second.published_at):
                        raise ValueError("discovery and article publication dates disagree")
                    with lock:
                        if accepting:
                            values.append(second)
                    report["read"] += 1
                except NoMatchingTargetError:
                    report["no_match"] += 1
                except Exception as exc:  # noqa: BLE001
                    report["errors"].append(f"{candidate.url}: {str(exc)[:160]}")
                    # An unavailable old seed must not veto successful updates
                    # from other channels; an unreadable newly discovered report
                    # remains a freshness caveat until peers cover that date.
                    if candidate.url in {row.url for row in relevant}:
                        with lock:
                            if accepting:
                                unresolved.append(candidate)
                finally:
                    with lock:
                        if accepting:
                            inflight[channel] = [row for row in inflight.get(channel, []) if row.url != candidate.url]
            report["completed"] = not capped and time.monotonic() < deadline
            with lock:
                if accepting:
                    reports[channel] = report

        # Each channel starts reading as soon as its own discovery completes.
        # Partial verified results are retained even if another channel hangs.
        def run_channel(url: str) -> None:
            try:
                channel_worker(url)
            except Exception as exc:  # noqa: BLE001
                with lock:
                    if accepting:
                        reports[_channel(url)] = {"discovery_ok": False, "completed": False,
                                                  "errors": [str(exc)[:160]]}

        pool = ThreadPoolExecutor(max_workers=len(_DISCOVERY))
        try:
            futures = [pool.submit(run_channel, url) for url in _DISCOVERY]
            _, pending = wait(futures, timeout=max(0, deadline - time.monotonic()))
            for future in pending:
                future.cancel()
            with lock:
                accepting = False
                values = list({(row.source_url, row.published_at, row.target_price): row for row in values}.values())
                unresolved = list(dict.fromkeys(unresolved + [row for rows in inflight.values() for row in rows]))
                latest = max((_source_day(row.published_at) for row in values), default="")
                unresolved_newer = any(not row.published_at or _source_day(row.published_at) > latest for row in unresolved)
                discovery_ok = any(row["discovery_ok"] and row["completed"] for row in reports.values()) and not unresolved_newer
                self.diagnostic = {"channels": reports, "unresolved_reports": [asdict(row) for row in unresolved],
                                   "timed_out_channels": len(pending), "discovery_ok": discovery_ok}
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        return values, discovery_ok


def target_display(value: AnalystTarget, *, price: float | None, retained: bool = False) -> ValuationDisplay:
    gap = value.target_price / price - 1 if price is not None and math.isfinite(price) and price > 0 else None
    return ValuationDisplay(
        ticker=TICKER, status="not_due" if retained else "current",
        intrinsic_value=value.target_price, implied_return=gap, hurdle_rate=0.10,
        currency_symbol="HK$", financial_as_of=_date(value.published_at).astimezone(HK).date().isoformat(),
        source_url=value.source_url, source_document_id=f"morgan-stanley:9992.HK:{value.published_at}:{value.target_price:g}",
        formula_id="analyst_target_price_gap", model_version="pop-mart-morgan-stanley-v1",
        return_label="IRR", value_label="公允价值", verified_at=value.verified_at,
        data_note="泡泡玛特沿用已核验目标价（最新报告本次未确认）" if retained else None,
    )


def refresh_target(*, state_dir: Path, config_dir: Path, checked_at: datetime,
                   price: float | None, provider: PopMartTargetProvider | None = None) -> ValuationDisplay | None:
    known = load_target(state_dir=state_dir, config_dir=config_dir, checked_at=checked_at)
    provider = provider or PopMartTargetProvider()
    try:
        values, discovery_ok = provider.fetch(known=known, checked_at=checked_at)
    except Exception as exc:  # noqa: BLE001
        logger.warning("pop_mart.refresh_failed reason=%s", exc)
        values, discovery_ok = [], False
    valid_values = []
    for row in values:
        try:
            valid_values.append(validate_target(row, checked_at=checked_at))
        except (ValueError, TypeError, AttributeError, OverflowError) as exc:
            logger.warning("pop_mart.invalid_observation type=%s", type(exc).__name__)
            discovery_ok = False
    values = valid_values
    selected = known
    retained = True
    if values:
        latest = max(_date(row.published_at).astimezone(HK).date() for row in values)
        newest = [row for row in values if _date(row.published_at).astimezone(HK).date() == latest]
        same_day_known = known and _date(known.published_at).astimezone(HK).date() == latest
        numbers = {row.target_price for row in newest}
        if same_day_known:
            numbers.add(known.target_price)
        if len(numbers) == 1 and (known is None or latest >= _date(known.published_at).astimezone(HK).date()):
            selected = max(newest, key=lambda row: (urlparse(row.source_url).hostname == "secure.aastocks.com", row.source_url))
            retained = not discovery_ok
        else:
            logger.warning("pop_mart.conflicting_or_older_report retained_last_verified=true")
    if selected is None:
        _save_source_diagnostic(state_dir, checked_at, provider, values, None, True)
        return None
    save_target(selected, state_dir=state_dir)
    _save_source_diagnostic(state_dir, checked_at, provider, values, selected, retained)
    logger.info("pop_mart.selected target=%.2f published=%s retained=%s source=%s",
                selected.target_price, selected.published_at, retained, selected.source_url)
    return target_display(selected, price=price, retained=retained)


def _save_source_diagnostic(state_dir: Path, checked_at: datetime, provider: PopMartTargetProvider,
                            values: list[AnalystTarget], selected: AnalystTarget | None, retained: bool) -> None:
    payload = {"checked_at": checked_at.isoformat(), "fetch": getattr(provider, "diagnostic", {}),
               "observations": [asdict(row) for row in values],
               "selected": asdict(selected) if selected else None, "retained": retained}
    path = state_dir / "pop_mart_source_diagnostic.json"
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)
    except (OSError, TypeError, ValueError) as exc:
        logger.warning("pop_mart.diagnostic_write_failed reason=%s", exc)
