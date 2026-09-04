"""Yahoo Finance 分发的 Morningstar 报告：首页公允价值核验与回退。"""

from __future__ import annotations

import logging
import math
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING

import requests
from PIL import Image, ImageEnhance, ImageOps

if TYPE_CHECKING:
    from src.valuation.morningstar import MorningstarFairValue, MorningstarSecurity

logger = logging.getLogger(__name__)

_SEARCH_URLS = (
    "https://query2.finance.yahoo.com/v1/finance/search",
    "https://query1.finance.yahoo.com/v1/finance/search",
)
_REPORT_URL = "https://finance.yahoo.com/research/reports/{report_id}/"
_SNAPSHOT_RE = re.compile(r'snapshotUrl\\?"\s*:\s*\\?"([^"\\]+)', re.I)
_VALUE_RE = re.compile(r"\b(\d{1,4}(?:[.,]\d{2})?)\s*(USD|HKD)(?![A-Z])", re.I)
_USER_AGENT = "daily-market-brief/1.0 (+Morningstar-report-validation)"

# 2026-08-29 逐只核验的 Yahoo/Morningstar 报告基线。搜索接口只负责发现比
# 基线更晚的报告；搜索临时限流时仍可读取这份已核验报告，而不是让备源失效。
_CURATED_REPORTS: dict[str, tuple[str, int, str]] = {
    "MSFT": (
        "MS_0P000003MH_AnalystReport_1785457664000",
        1785457664000,
        "0P000003MH_20260730194949.jpg",
    ),
    "COST": (
        "MS_0P000001IK_AnalystReport_1780026172000",
        1780026172000,
        "0P000001IK_20260528224402.jpg",
    ),
    "AAPL": (
        "MS_0P000000GY_AnalystReport_1785885746000",
        1785885746000,
        "0P000000GY_20260804184856.jpg",
    ),
    "NVDA": (
        "MS_0P000003RE_AnalystReport_1787800705000",
        1787800705000,
        "0P000003RE_20260826221937.jpg",
    ),
    "TSM": (
        "MS_0P000005AR_AnalystReport_1784245434000",
        1784245434000,
        "0P000005AR_20260716184620.jpg",
    ),
    "MCO": (
        "MS_0P000003P7_AnalystReport_1785162536000",
        1785162536000,
        "0P000003P7_20260727093002.jpg",
    ),
    "GOOG": (
        "MS_0P00012BBI_AnalystReport_1784780369000",
        1784780369000,
        "0P00012BBI_20260722235926.jpg",
    ),
    "BRK.B": (
        "MS_0P000000RD_AnalystReport_1786927320000",
        1786927320000,
        "0P000000RD_20260816194330.jpg",
    ),
    "KO": (
        "MS_0P000001BW_AnalystReport_1785267026000",
        1785267026000,
        "0P000001BW_20260728143117.jpg",
    ),
    "AXP": (
        "MS_0P000000CU_AnalystReport_1784906237000",
        1784906237000,
        "0P000000CU_20260724101823.jpg",
    ),
    "MA": (
        "MS_0P00005U6B_AnalystReport_1785421221000",
        1785421221000,
        "0P00005U6B_20260730092148.jpg",
    ),
    "LIN": (
        "MS_0P000004FA_AnalystReport_1785814581000",
        1785814581000,
        "0P000004FA_20260803230129.jpg",
    ),
}


class YahooMorningstarProvider:
    """读取 Yahoo 合法分发的 Morningstar 报告缩略图，不需要用户登录。"""

    def __init__(
        self,
        *,
        timeout: float = 30.0,
        session: requests.Session | None = None,
        tesseract_path: str | None = None,
    ) -> None:
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", _USER_AGENT)
        self.tesseract_path = tesseract_path or shutil.which("tesseract")
        self._last_search_at: float | None = None
        self._search_disabled_reason: str | None = None

    def _search(self, params: Mapping[str, object]) -> Mapping[str, object]:
        if self._search_disabled_reason:
            raise ValueError(self._search_disabled_reason)
        errors: list[str] = []
        for attempt in range(2):
            for url in _SEARCH_URLS:
                if self._last_search_at is not None:
                    elapsed = time.monotonic() - self._last_search_at
                    if elapsed < 0.5:
                        time.sleep(0.5 - elapsed)
                try:
                    response = self.session.get(url, params=params, timeout=min(self.timeout, 12.0))
                except requests.RequestException as exc:
                    errors.append(f"{url}: {type(exc).__name__}")
                    continue
                self._last_search_at = time.monotonic()
                if response.status_code in {429, 500, 502, 503, 504}:
                    errors.append(f"{url}: HTTP {response.status_code}")
                    continue
                response.raise_for_status()
                payload = response.json()
                if isinstance(payload, Mapping) and payload.get("researchReports"):
                    return payload
                errors.append(f"{url}: 未返回研究报告")
            if attempt < 1:
                time.sleep(2**attempt)
        self._search_disabled_reason = "Yahoo 搜索本轮熔断: " + "; ".join(errors[-4:])
        raise ValueError(self._search_disabled_reason)

    @staticmethod
    def _symbol(ticker: str) -> str | None:
        if ticker.endswith(".HK"):
            # Yahoo 暂无这两只港股的同一上市口径 Morningstar 报告。腾讯 ADR
            # 也不能直接替代 0700.HK 的 HKD 公允价值。
            return None
        return "BRK-B" if ticker == "BRK.B" else ticker

    def _latest_report(
        self, security: MorningstarSecurity
    ) -> tuple[str, datetime, str, str | None]:
        symbol = self._symbol(security.ticker)
        if symbol is None:
            raise ValueError("Yahoo 无同一港股上市口径的 Morningstar 报告")
        reports: list[Mapping[str, object]] = []
        company_key = security.company_name.lower().split()[0]
        curated = _CURATED_REPORTS.get(security.ticker)
        if curated is not None:
            reports.append(
                {
                    "id": curated[0],
                    "reportDate": curated[1],
                    "provider": "Morningstar",
                    "reportHeadline": f"Analyst Report: {security.company_name}",
                    "snapshotUrl": (
                        f"https://s.yimg.com/uc/fin/img/ms-reports-thumbnails/{curated[2]}"
                    ),
                }
            )
        try:
            payload = self._search(
                {
                    "q": symbol,
                    "quotesCount": 3,
                    "newsCount": 0,
                    "listsCount": 0,
                    "enableFuzzyQuery": "false",
                    "enableNavLinks": "false",
                    "enableResearchReports": "true",
                    "researchReportsCount": 20,
                    "lang": "en-US",
                    "region": "US",
                }
            )
            reports.extend(
                row
                for row in payload.get("researchReports", [])
                if isinstance(row, Mapping)
                and row.get("provider") == "Morningstar"
                and "AnalystReport" in str(row.get("id") or "")
                and company_key in str(row.get("reportHeadline") or "").lower()
            )
        except (requests.RequestException, ValueError) as exc:
            if not reports:
                raise
            logger.info(
                "morningstar.yahoo_discovery_fallback ticker=%s reason=%s",
                security.ticker,
                str(exc)[:180],
            )
        if not reports:
            raise ValueError("Yahoo 未返回 Morningstar 个股报告")
        report = max(reports, key=lambda row: int(row.get("reportDate") or 0))
        headline = str(report.get("reportHeadline") or "")
        if company_key not in headline.lower():
            raise ValueError("Yahoo Morningstar 报告与标的公司不匹配")
        report_id = str(report["id"])
        report_ms = int(report.get("reportDate") or 0)
        if report_ms <= 0:
            raise ValueError("Yahoo Morningstar 报告缺发布日期")
        published = datetime.fromtimestamp(report_ms / 1000, tz=UTC)
        return (
            report_id,
            published,
            _REPORT_URL.format(report_id=report_id),
            str(report.get("snapshotUrl") or "") or None,
        )

    def _snapshot_url(self, report_url: str) -> str:
        response = self.session.get(report_url, timeout=self.timeout)
        response.raise_for_status()
        match = _SNAPSHOT_RE.search(response.text)
        if match is None:
            raise ValueError("Yahoo Morningstar 报告缺首页快照")
        snapshot_url = match.group(1).replace(r"\/", "/")
        if not snapshot_url.startswith("https://s.yimg.com/"):
            raise ValueError("Yahoo Morningstar 报告快照域名不合规")
        return snapshot_url

    def _ocr_once(self, image: Image.Image, *, psm: int) -> tuple[float, str]:
        if not self.tesseract_path:
            raise ValueError("系统未安装 tesseract，无法读取 Yahoo 报告数值")
        # Morningstar 报告首页版式固定：第二个指标栏为 Fair Value Estimate。
        width, height = image.size
        crop = image.crop(
            (
                round(width * 105 / 612),
                round(height * 65 / 792),
                round(width * 225 / 612),
                round(height * 145 / 792),
            )
        )
        crop = ImageOps.grayscale(crop).resize((960, 640))
        crop = ImageEnhance.Contrast(crop).enhance(2.0)
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                suffix=".png", prefix="morningstar-", dir=Path.cwd(), delete=False
            ) as handle:
                temp_path = Path(handle.name)
            crop.save(temp_path)
            result = subprocess.run(
                [
                    self.tesseract_path,
                    str(temp_path),
                    "stdout",
                    "--psm",
                    str(psm),
                    "-c",
                    "tessedit_char_whitelist=0123456789.USDHK",
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=20,
            )
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
        match = _VALUE_RE.search(result.stdout)
        if match is None:
            raise ValueError("Yahoo Morningstar 报告首页未识别出公允价值")
        value = float(match.group(1).replace(",", "."))
        currency = match.group(2).upper()
        if not math.isfinite(value) or value <= 0:
            raise ValueError("Yahoo Morningstar 公允价值无效")
        return value, currency

    def _read(
        self,
        security: MorningstarSecurity,
        *,
        checked_at: datetime,
    ) -> MorningstarFairValue:
        from src.valuation.morningstar import MorningstarFairValue

        report_id, published, report_url, curated_snapshot_url = self._latest_report(security)
        snapshot_url = curated_snapshot_url or self._snapshot_url(report_url)
        if not snapshot_url.startswith("https://s.yimg.com/"):
            raise ValueError("Yahoo Morningstar 报告快照域名不合规")
        response = self.session.get(snapshot_url, timeout=self.timeout)
        response.raise_for_status()
        image = Image.open(BytesIO(response.content))
        first = self._ocr_once(image, psm=6)
        second = self._ocr_once(image, psm=11)
        if first != second:
            raise ValueError("Yahoo Morningstar 报告双重 OCR 结果不一致")
        value, currency = first
        if currency != security.currency:
            raise ValueError(f"Yahoo 报告币种 {currency} 与固定上市口径 {security.currency} 不符")
        return MorningstarFairValue(
            ticker=security.ticker,
            provider_code=security.provider_code,
            fair_value=value,
            currency=currency,
            rating_type="published-research",
            # 这是“最新报告再次确认该估值”的证据日期，不假定报告日一定改值。
            fair_value_updated_at=published.date().isoformat(),
            report_published_at=published.isoformat(),
            retrieved_at=checked_at.astimezone(UTC).isoformat(),
            source_provider="Morningstar report distributed by Yahoo Finance",
            source_url=report_url,
            observation_count=2,
            extraction_verified=True,
        )

    def fetch_all(
        self,
        securities: Mapping[str, MorningstarSecurity],
        *,
        checked_at: datetime,
    ) -> tuple[dict[str, MorningstarFairValue], dict[str, str]]:
        values: dict[str, MorningstarFairValue] = {}
        failures: dict[str, str] = {}
        for ticker, security in securities.items():
            try:
                values[ticker] = self._read(security, checked_at=checked_at)
            except Exception as exc:  # noqa: BLE001
                failures[ticker] = str(exc)[:240]
                logger.info(
                    "morningstar.yahoo_unavailable ticker=%s reason=%s",
                    ticker,
                    failures[ticker],
                )
        return values, failures
