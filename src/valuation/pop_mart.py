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
import time
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
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
_HOSTS = {"finance.sina.com.cn", "secure.aastocks.com"}
_MOBILE = "https://secure.aastocks.com/tc/mobile/News.aspx?NewsID={}&NewsSource=HK6"
_SEED_ARTICLES = (
    _MOBILE.format("NOW.1539842"),
    "https://finance.sina.com.cn/stock/usstock/c/2026-08-21/doc-ininztwt6444665.shtml",
)
_DISCOVERY = (
    "https://www.aastocks.com/tc/stocks/analysis/stock-aafn/09992/0/hk-stock-news/1",
    "https://stock.finance.sina.com.cn/hkstock/quotes/09992.html",
)
_POP = re.compile(r"泡泡[玛瑪]特")
_BROKER = re.compile(r"摩根士丹利|大摩|Morgan Stanley", re.I)
_OTHER_BROKER = re.compile(r"摩根大通|摩通|小摩|高盛|美銀|美银|滙豐|汇丰|瑞銀|瑞银|富瑞|花旗|野村|交銀|交银")
_TRANS = str.maketrans("標價從調維將為於幣給瑪", "标价从调维将为于币给玛")


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
    return parsed.path == "/tc/mobile/News.aspx" and bool(re.search(r"NewsID=NOW\.\d+", parsed.query))


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
    if urlparse(url).hostname == "finance.sina.com.cn":
        title_node = soup.find("h1")
        body_node = soup.select_one("#artibody")
        published_node = soup.select_one('meta[property="article:published_time"]')
        if not title_node or not body_node or not published_node:
            raise ValueError("Sina article/date missing")
        published = _date(str(published_node.get("content", "")))
    else:
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
    title = title_node.get_text(" ", strip=True)
    body = body_node.get_text(" ", strip=True)
    if not _POP.search(title) or not _BROKER.search(title) or _OTHER_BROKER.search(title + body):
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
    """Bounded daily discovery on two free channels; verify full article text."""

    def __init__(self, *, budget_seconds: float = 24):
        self.budget_seconds = budget_seconds

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

    def _discover(self, url: str, *, deadline: float) -> list[str]:
        html = self._get(url, deadline=deadline)
        soup = BeautifulSoup(html, "lxml")
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
                found.append(candidate)
        if not news_found:
            raise ValueError("stock news listing missing; cannot confirm discovery")
        return list(dict.fromkeys(found))[:6]

    def fetch(self, *, known: AnalystTarget | None, checked_at: datetime) -> tuple[list[AnalystTarget], bool]:
        deadline = time.monotonic() + self.budget_seconds
        pool = ThreadPoolExecutor(max_workers=4)
        discovery_ok = False
        candidates = ([known.source_url] if known else []) + list(_SEED_ARTICLES)
        values = []
        try:
            discoveries = [pool.submit(self._discover, url, deadline=deadline) for url in _DISCOVERY]
            done, pending = wait(discoveries, timeout=max(0, deadline - time.monotonic()))
            for future in done:
                try:
                    urls = future.result()
                    discovery_ok = True
                    candidates.extend(urls)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("pop_mart.discovery_failed reason=%s", exc)
            for future in pending:
                future.cancel()

            def read(url: str) -> AnalystTarget:
                first = parse_article(url, self._get(url, deadline=deadline), checked_at=checked_at)
                second = parse_article(url, self._get(url, deadline=deadline), checked_at=checked_at)
                if (first.target_price, first.published_at) != (second.target_price, second.published_at):
                    raise ValueError("article changed between verification reads")
                return second

            reads = [pool.submit(read, url) for url in list(dict.fromkeys(candidates))[:8]]
            done, pending = wait(reads, timeout=max(0, deadline - time.monotonic()))
            for future in done:
                try:
                    values.append(future.result())
                except Exception as exc:  # noqa: BLE001
                    logger.warning("pop_mart.article_rejected reason=%s", exc)
                    discovery_ok = False
            for future in pending:
                future.cancel()
                discovery_ok = False
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
        values = [validate_target(row, checked_at=checked_at) for row in values]
    except Exception as exc:  # noqa: BLE001
        logger.warning("pop_mart.refresh_failed reason=%s", exc)
        values, discovery_ok = [], False
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
        return None
    save_target(selected, state_dir=state_dir)
    logger.info("pop_mart.selected target=%.2f published=%s retained=%s source=%s",
                selected.target_price, selected.published_at, retained, selected.source_url)
    return target_display(selected, price=price, retained=retained)
