"""QQQM/QQQ v1.5 估值：DeepSeek 只整理当天输入，Python 固定公式复算。"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from src.processors.llm_client import LLMClient
from src.valuation.models import ValuationDisplay

logger = logging.getLogger(__name__)

_CACHE_NAME = "qqqm_valuation.json"
_STALE_DAYS = 14
_FEE = 0.0015
_DISCOUNT = 0.10
_PE_EXIT = 24.65
_WEIGHTS = (0.20, 0.40, 0.40)
_BEIJING = ZoneInfo("Asia/Shanghai")
_ALLOWED_DOMAINS = (
    "invesco.com",
    "gurufocus.com",
    "historyofmarket.com",
    "fred.stlouisfed.org",
)


@dataclass(frozen=True)
class QQQMInputs:
    """同一 D_anchor 的每日输入；年更参数仍由代码冻结。"""

    price: float
    nav_anchor: float
    pe_ttm: float
    pe_pair_t: float
    pe_pair_f: float
    div_ttm: float
    data_date: str
    source_urls: tuple[str, ...] = ()
    stale_days: int = 0


@dataclass(frozen=True)
class QQQMResult:
    value: float
    implied_return: float | None
    inputs: QQQMInputs
    warning: str | None = None


def build_qqqm_prompt(*, checked_at: datetime, price: float) -> str:
    """给 DeepSeek 的固定取数指令；模型不可改公式和参数。"""
    return f"""为 QQQM 生成当天估值输入 JSON。当前价格 P={price:.6f} USD，由程序行情采集，不能修改。
数据日 D_anchor 不得晚于北京时间 {checked_at.astimezone(_BEIJING).date().isoformat()}，且 NAV_anchor、PE_ttm、PE_pair_t、PE_pair_f、DIV_ttm 必须同一数据日。
请只搜索并引用以下公开来源：QQQM 的 Invesco 官方 NAV/分红页面；GuruFocus Nasdaq 100 PE Ratio（PE_ttm、PE_exit）；historyofmarket.com 的 NDX trailing/forward PE 配对；必要时 FRED DGS3MO 仅作记录。若某项无法确认，返回 null，不要猜测或用旧值。
固定规则（不可改）：E0= NAV_anchor/PE_ttm；k=DIV_ttm/E0；g_mkt=PE_pair_t/PE_pair_f-1；三情景权重=20%/40%/40%；第1年盈利分别为 E0×1.08、E0×1.12、E0×(1+g_mkt)，之后保守 8%/5%、基准 12%/6%、乐观 15%/7%；PE_exit=24.65；QQQM fee=0.15%；折现率=10%；预测期=10年。你只整理输入，不计算 IV 或 IRR。
只返回 JSON：{{"status":"ok|needs_review","data":{{"nav_anchor":数值,"pe_ttm":数值,"pe_pair_t":数值,"pe_pair_f":数值,"div_ttm":数值,"data_date":"YYYY-MM-DD","source_urls":["https://..."]}},"citations":[{{"field":"字段","source":"URL","date":"YYYY-MM-DD","quote":"不超过25字"}}]}}。status=ok 必须至少五条引文且每个输入字段都有对应引文。"""


def _number(data: dict[str, Any], key: str) -> float:
    value = data.get(key)
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"QQQM {key} 缺失或非数值") from exc
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"QQQM {key} 必须为正有限数")
    return number


def parse_qqqm_inputs(text: str, *, price: float, checked_at: datetime) -> QQQMInputs:
    if not math.isfinite(price) or price <= 0:
        raise ValueError("QQQM 现价必须为正有限数")
    cleaned = text.strip()
    if cleaned.startswith("```"):
        parts = cleaned.split("\n", 1)
        if len(parts) != 2:
            raise ValueError("QQQM JSON 代码块格式无效")
        cleaned = parts[1].rsplit("```", 1)[0].strip()
    payload = json.loads(cleaned)
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        raise ValueError("DeepSeek 未返回 QQQM ok 输入")
    data = payload.get("data")
    citations = payload.get("citations")
    if not isinstance(data, dict) or not isinstance(citations, list) or len(citations) < 5:
        raise ValueError("QQQM 输入缺数据或引文")
    required_fields = {"nav_anchor", "pe_ttm", "pe_pair_t", "pe_pair_f", "div_ttm", "data_date"}
    cited = {
        str(item.get("field", ""))
        for item in citations
        if isinstance(item, dict) and str(item.get("source", "")).strip().startswith("https://")
    }
    if not required_fields <= cited:
        raise ValueError("QQQM 每个输入字段都必须有来源引文")
    data_date = str(data.get("data_date", "")).strip()
    try:
        anchor = date.fromisoformat(data_date)
    except ValueError as exc:
        raise ValueError("QQQM data_date 无效") from exc
    checked_date = checked_at.astimezone(_BEIJING).date()
    stale_days = (checked_date - anchor).days
    if stale_days < 0 or stale_days > _STALE_DAYS:
        raise ValueError(f"QQQM 数据日距今日 {stale_days} 天，超过 14 天或在未来")
    urls = tuple(
        dict.fromkeys(
            str(url).strip()
            for url in (data.get("source_urls") or [])
            if str(url).strip().startswith("https://")
        )
    )
    if len(urls) < 3:
        raise ValueError("QQQM 来源 URL 少于 3 个")
    if any(
        not any(
            (host := (urlparse(url).hostname or "").lower()) == domain
            or host.endswith(f".{domain}")
            for domain in _ALLOWED_DOMAINS
        )
        for url in urls
    ):
        raise ValueError("QQQM 来源含不在允许清单内的域名")
    for item in citations:
        if not isinstance(item, dict) or str(item.get("field", "")) not in required_fields:
            continue
        source = str(item.get("source", "")).strip()
        if source not in urls or str(item.get("date", "")).strip() != data_date:
            raise ValueError("QQQM 引文来源或数据日不匹配")
    inputs = QQQMInputs(
        price=price,
        nav_anchor=_number(data, "nav_anchor"),
        pe_ttm=_number(data, "pe_ttm"),
        pe_pair_t=_number(data, "pe_pair_t"),
        pe_pair_f=_number(data, "pe_pair_f"),
        div_ttm=_number(data, "div_ttm"),
        data_date=data_date,
        source_urls=urls,
        stale_days=stale_days,
    )
    if inputs.pe_pair_f <= 0 or not -0.10 <= inputs.pe_pair_t / inputs.pe_pair_f - 1 <= 0.40:
        raise ValueError("QQQM 市场隐含增速超出固定校验区间")
    e0 = inputs.nav_anchor / inputs.pe_ttm
    payout = inputs.div_ttm / e0
    if not 0.05 <= payout <= 0.40:
        raise ValueError("QQQM 派息率超出固定校验区间")
    return inputs


def _scenario(
    e0: float, k: float, first: float, g1: float, g2: float, exit_pe: float, rate: float
) -> float:
    earnings = first
    pv = 0.0
    for year in range(1, 11):
        if year > 1:
            earnings *= 1 + (g1 if year <= 5 else g2)
        pv += k * earnings / ((1 + rate) ** year)
    return pv + exit_pe * earnings / ((1 + rate) ** 10)


def calculate_qqqm(inputs: QQQMInputs) -> QQQMResult:
    e0 = inputs.nav_anchor / inputs.pe_ttm
    k = inputs.div_ttm / e0
    g_mkt = inputs.pe_pair_t / inputs.pe_pair_f - 1
    e_fwd = e0 * (1 + g_mkt)

    def value(rate: float) -> float:
        return sum(
            weight * scenario
            for weight, scenario in zip(
                _WEIGHTS,
                (
                    _scenario(e0, k, e0 * 1.08, 0.08, 0.05, _PE_EXIT * 0.81, rate),
                    _scenario(e0, k, e0 * 1.12, 0.12, 0.06, _PE_EXIT, rate),
                    _scenario(e0, k, e_fwd, 0.15, 0.07, _PE_EXIT, rate),
                ),
                strict=True,
            )
        )

    intrinsic = value(_DISCOUNT + _FEE)
    low, high = -0.50, 1.00
    if value(low) < inputs.price or value(high) > inputs.price:
        implied = None
    else:
        for _ in range(100):
            mid = (low + high) / 2
            if value(mid) > inputs.price:
                low = mid
            else:
                high = mid
        implied = (low + high) / 2 - _FEE
    return QQQMResult(value=intrinsic, implied_return=implied, inputs=inputs)


def _from_cache(path: Path, *, price: float, checked_at: datetime) -> QQQMResult | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        inputs = QQQMInputs(**payload["inputs"])
        inputs = QQQMInputs(**{**asdict(inputs), "price": price})
        anchor = date.fromisoformat(inputs.data_date)
        stale_days = (checked_at.astimezone(_BEIJING).date() - anchor).days
        if stale_days < 0 or stale_days > _STALE_DAYS:
            return None
        return calculate_qqqm(replace(inputs, stale_days=stale_days))
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def prepare_qqqm_display(
    *, price: float, client: LLMClient, state_dir: Path, checked_at: datetime
) -> ValuationDisplay:
    """每日一次 DeepSeek 输入整理，失败时仅回退 14 日内完整同日快照。"""
    prompt = build_qqqm_prompt(checked_at=checked_at, price=price)
    response = client.search_web(
        prompt,
        allowed_domains=_ALLOWED_DOMAINS,
        task_extra="QQQM v1.5：只整理公开数据输入，禁止改变固定公式；Python 将复算最终公允价值和 IRR。",
        max_output_tokens=1800,
        # 网页搜索需要等待来源页面聚合；单次最多 90 秒，仍受 LLM 总预算约束。
        timeout=90,
    )
    result: QQQMResult | None = None
    warning: str | None = None
    if response.text:
        try:
            result = calculate_qqqm(
                parse_qqqm_inputs(response.text, price=price, checked_at=checked_at)
            )
            state_dir.mkdir(parents=True, exist_ok=True)
            (state_dir / _CACHE_NAME).write_text(
                json.dumps({"inputs": asdict(result.inputs)}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            warning = f"QQQM 当日输入校验失败：{str(exc)[:120]}"
    else:
        warning = f"QQQM DeepSeek 输入失败：{response.error or '空响应'}"
    if result is None:
        result = _from_cache(state_dir / _CACHE_NAME, price=price, checked_at=checked_at)
        if result is not None:
            warning = (
                f"{warning}；沿用 14 日内同日输入快照" if warning else "沿用 14 日内同日输入快照"
            )
    if result is None:
        return ValuationDisplay(
            ticker="QQQM",
            status="source_unavailable",
            hurdle_rate=_DISCOUNT,
            currency_symbol="$",
            return_label="IRR",
            value_label="公允价值",
            warnings=(warning or "QQQM 没有可验证的同日输入",),
        )
    return ValuationDisplay(
        ticker="QQQM",
        status="not_due" if result.inputs.stale_days else "current",
        intrinsic_value=result.value,
        implied_return=result.implied_return,
        hurdle_rate=_DISCOUNT,
        currency_symbol="$",
        financial_as_of=result.inputs.data_date,
        source_url=result.inputs.source_urls[0],
        source_document_id=f"qqqm-v1.5:{result.inputs.data_date}",
        formula_id="qqqm_three_scenario_cashflow_v1_5",
        model_version="1.5",
        return_label="IRR",
        value_label="公允价值",
        warnings=(warning,) if warning else (),
    )
