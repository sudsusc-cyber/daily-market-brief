"""QQQM/QQQ v1.5 估值：DeepSeek 只整理当天输入，Python 固定公式复算。"""

from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from src.processors.llm_client import LLMClient
from src.utils.holidays import is_us_market_open
from src.valuation.models import ValuationDisplay
from src.valuation.qqqm_sources import PE_URL, fetch_source_packet, latest_closed_date

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
    "stockanalysis.com",
)


@dataclass(frozen=True)
class QQQMInputs:
    """同一 D_anchor 的每日输入；年更参数仍由代码冻结。"""

    price: float
    nav_anchor: float
    pe_ttm: float
    pe_pair_t: float | None
    pe_pair_f: float | None
    div_ttm: float
    data_date: str
    source_urls: tuple[str, ...] = ()
    stale_days: int = 0
    fwd_date: str | None = None


@dataclass(frozen=True)
class QQQMResult:
    value: float
    implied_return: float | None
    inputs: QQQMInputs
    warning: str | None = None


def build_qqqm_prompt(*, checked_at: datetime, price: float) -> str:
    """给 DeepSeek 的固定取数指令；模型不可改公式和参数。"""
    return f"""为 QQQM 生成当天估值输入 JSON。当前价格 P={price:.6f} USD，由程序行情采集，不能修改。
数据日 D_anchor 是最近已收盘美股交易日 {latest_closed_date(checked_at).isoformat()}。NAV_anchor 和 PE_ttm 必须同为 D_anchor 日，DIV_ttm 是截至该日的过去12个月分红合计。
GuruFocus 唯一目标页面是 {PE_URL}，不是 FRA:NDX 的 Nordex 股票，也不是 QQQM 基金本身的 PE。若页面标题和旧统计表日期不同，取实际数据日匹配的最新读数。最多搜索3次；缺失则立即输出 needs_review JSON，不输出过程叙述。
优先搜索 site:gurufocus.com/economic_indicators/6778/nasdaq-100-pe-ratio "As of {latest_closed_date(checked_at).isoformat()}"。若原页返回403，可读取搜索结果中完全相同URL的公开标题或原文摘要，但必须同时明确包含 Nasdaq 100 PE Ratio、数值与匹配的数据日期；不得用其他网址的二手转述或猜测。
请搜索 QQQM 的 Invesco 官方 NAV、GuruFocus Nasdaq 100 PE Ratio（PE_ttm）以及 Invesco 分红历史（允许 stockanalysis.com/etf/qqqm/dividend/）；可选的 PE_pair_t/PE_pair_f 仅取 historyofmarket.com 的同日 NDX trailing/forward PE 配对，记录 fwd_date，距 D_anchor 最多3个交易日。找不到配对时将两个值及 fwd_date 写 null，仍返回已确认的 NAV、PE_ttm、DIV_ttm；不要反复搜索配对。无法确认核心字段时 status=needs_review，不可猜测。
固定规则（不可改）：E0= NAV_anchor/PE_ttm；k=DIV_ttm/E0；g_mkt=PE_pair_t/PE_pair_f-1；三情景权重=20%/40%/40%；第1年盈利分别为 E0×1.08、E0×1.12、E0×(1+g_mkt)，之后保守 8%/5%、基准 12%/6%、乐观 15%/7%；PE_exit=24.65；QQQM fee=0.15%；折现率=10%；预测期=10年。你只整理输入，不计算 IV 或 IRR。
只返回 JSON：{{"status":"ok|needs_review","data":{{"nav_anchor":数值,"pe_ttm":数值,"pe_pair_t":数值或null,"pe_pair_f":数值或null,"fwd_date":"YYYY-MM-DD或null","div_ttm":数值,"data_date":"YYYY-MM-DD","source_urls":["https://..."]}},"citations":[{{"field":"nav_anchor|pe_ttm|div_ttm|pe_pair_t|pe_pair_f","source":"URL","date":"真实数据日YYYY-MM-DD","quote":"不超过25字"}}]}}。核心字段 nav_anchor、pe_ttm、div_ttm 各有引文；有配对数值才需要配对引文。"""


def _number(data: dict[str, Any], key: str) -> float:
    value = data.get(key)
    if isinstance(value, bool):
        raise ValueError(f"QQQM {key} 不得为布尔值")
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
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        # Responses web_search 可能把检索摘录和最终 JSON 一起放入 output；
        # 只从文本中提取带 status/data 的完整对象，后续仍走全部字段与来源闸门。
        decoder = json.JSONDecoder()
        payload = None
        for match in re.finditer(r"\{", cleaned):
            try:
                candidate, _ = decoder.raw_decode(cleaned[match.start() :])
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict) and {"status", "data", "citations"} <= candidate.keys():
                payload = candidate
                break
        if payload is None:
            raise
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        raise ValueError("DeepSeek 未返回 QQQM ok 输入")
    data = payload.get("data")
    citations = payload.get("citations")
    if not isinstance(data, dict) or not isinstance(citations, list) or len(citations) < 3:
        raise ValueError("QQQM 输入缺数据或引文")
    required_fields = {"nav_anchor", "pe_ttm", "div_ttm"}
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
    if anchor > latest_closed_date(checked_at) or not is_us_market_open(anchor):
        raise ValueError("QQQM 数据日不是已收盘交易日或属于未来数据")
    urls = tuple(
        dict.fromkeys(
            str(url).strip()
            for url in (data.get("source_urls") or [])
            if str(url).strip().startswith("https://")
        )
    )
    if len(urls) < 2:
        raise ValueError("QQQM 来源 URL 少于 2 个")
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
        permitted = {"nav_anchor": ("invesco.com",), "pe_ttm": ("gurufocus.com",),
                     "div_ttm": ("invesco.com", "stockanalysis.com")}[item["field"]]
        host = (urlparse(source).hostname or "").lower()
        if not any(host == domain or host.endswith(f".{domain}") for domain in permitted):
            raise ValueError(f"QQQM {item['field']} 不符合指定数据源")
        if item["field"] == "pe_ttm" and urlparse(source).path.rstrip("/") != urlparse(PE_URL).path:
            raise ValueError("QQQM PE 来源不是指定 Nasdaq 100 指数页面")
    pair_t = pair_f = None
    fwd_date = None
    try:
        pair_t = _number(data, "pe_pair_t")
        pair_f = _number(data, "pe_pair_f")
        pair_rows = [item for item in citations if isinstance(item, dict)
                     and item.get("field") in {"pe_pair_t", "pe_pair_f"}]
        if {item.get("field") for item in pair_rows} != {"pe_pair_t", "pe_pair_f"}:
            raise ValueError("配对引文缺失")
        dates = {str(item.get("date", "")) for item in pair_rows}
        sources = {str(item.get("source", "")) for item in pair_rows}
        if len(dates) != 1 or len(sources) != 1:
            raise ValueError("配对不同日同源")
        pair_url = next(iter(sources))
        host = (urlparse(pair_url).hostname or "").lower()
        if pair_url not in urls or host not in {"historyofmarket.com", "www.historyofmarket.com"}:
            raise ValueError("配对数据源无效")
        fwd_date = str(data.get("fwd_date") or next(iter(dates)))
        forward = date.fromisoformat(fwd_date)
        if dates != {fwd_date} or forward > checked_date:
            raise ValueError("配对日期无效")
        start, end = sorted((anchor, forward))
        gap = sum(is_us_market_open(start + timedelta(days=i))
                  for i in range(1, (end - start).days + 1))
        if gap > 3 or not -0.10 <= pair_t / pair_f - 1 <= 0.40:
            raise ValueError("配对超过时效或增速范围")
    except (ValueError, TypeError):
        pair_t = pair_f = None
        fwd_date = None
    inputs = QQQMInputs(
        price=price,
        nav_anchor=_number(data, "nav_anchor"),
        pe_ttm=_number(data, "pe_ttm"),
        pe_pair_t=pair_t,
        pe_pair_f=pair_f,
        div_ttm=_number(data, "div_ttm"),
        data_date=data_date,
        source_urls=urls,
        stale_days=stale_days,
        fwd_date=fwd_date,
    )
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
    e_fwd = (e0 * inputs.pe_pair_t / inputs.pe_pair_f
             if inputs.pe_pair_t and inputs.pe_pair_f else None)

    def value(rate: float) -> float:
        if e_fwd is None:
            return _scenario(e0, k, e0 * 1.12, 0.12, 0.06, _PE_EXIT, rate)
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
    return QQQMResult(value=intrinsic, implied_return=implied, inputs=inputs,
                      warning="B类：乐观缺失，展示基准价值" if e_fwd is None else None)


def _from_cache(path: Path, *, price: float, checked_at: datetime) -> QQQMResult | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        # Re-run all value/date/source gates, not just the cache age check.
        inputs = parse_qqqm_inputs(payload["source_response"], price=price, checked_at=checked_at)
        return calculate_qqqm(inputs)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def prepare_qqqm_display(
    *, price: float, client: LLMClient, state_dir: Path, checked_at: datetime
) -> ValuationDisplay:
    """每日一次 DeepSeek 输入整理，失败时仅回退 14 日内完整同日快照。"""
    prompt = build_qqqm_prompt(checked_at=checked_at, price=price)
    source_packet = fetch_source_packet(checked_at=checked_at)
    if source_packet is not None:
        prompt += (
            "\n以下是程序本次直接从官方公开 API 获取并核验的完整来源包。"
            "这些字段照抄，不必重复搜索 NAV/分红/PE配对。只需联网打开指定 GuruFocus 页面补齐 pe_ttm 与引文，"
            "把它的 URL 加入 source_urls，合并为要求的最终 JSON；配对为 null 时保留 null。\n"
            + json.dumps(source_packet, ensure_ascii=False)
        )
    response = client.search_web(
        prompt,
        allowed_domains=_ALLOWED_DOMAINS,
        market_data=True,
        task_extra="QQQM v1.5：只整理公开数据输入，禁止改变固定公式；Python 将复算最终公允价值和 IRR。",
        max_output_tokens=1800,
        # 网页搜索需要等待来源页面聚合；单次最多 90 秒，仍受 LLM 总预算约束。
        timeout=90,
    )
    result: QQQMResult | None = None
    warning: str | None = None
    if response.text:
        try:
            inputs = parse_qqqm_inputs(response.text, price=price, checked_at=checked_at)
            if source_packet is not None:
                for field in ("nav_anchor", "div_ttm", "pe_pair_t", "pe_pair_f", "fwd_date", "data_date"):
                    actual, expected = getattr(inputs, field), source_packet[field]
                    if isinstance(expected, float) and isinstance(actual, (float, int)):
                        matches = math.isclose(actual, expected, rel_tol=1e-8)
                    else:
                        matches = actual == expected
                    if not matches:
                        raise ValueError(f"QQQM {field} 被模型改写，与官方原始数据不符")
            result = calculate_qqqm(inputs)
            state_dir.mkdir(parents=True, exist_ok=True)
            cache_path = state_dir / _CACHE_NAME
            temporary = cache_path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps({"inputs": asdict(result.inputs), "source_response": response.text}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(cache_path)
        except OSError as exc:
            # A valid live result remains usable when persisting its cache fails.
            warning = f"QQQM 快照保存失败：{type(exc).__name__}"
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
        logger.warning("valuation.qqqm_unavailable reason=%s", warning)
        return ValuationDisplay(
            ticker="QQQM",
            status="source_unavailable",
            hurdle_rate=_DISCOUNT,
            currency_symbol="$",
            return_label="IRR",
            value_label="公允价值",
            warnings=(warning or "QQQM 没有可验证的同日输入",),
        )
    logger.info(
        "valuation.qqqm_ready value=%.4f gap_return=%.6f data_date=%s fallback=%s",
        result.value, result.value / price - 1, result.inputs.data_date, bool(warning),
    )
    return ValuationDisplay(
        ticker="QQQM",
        status="not_due" if warning else "current",
        intrinsic_value=result.value,
        # Email IRR is the user's value-gap return, not the document's 10Y IRR.
        implied_return=result.value / price - 1,
        hurdle_rate=_DISCOUNT,
        currency_symbol="$",
        financial_as_of=result.inputs.data_date,
        source_url=result.inputs.source_urls[0],
        source_document_id=f"qqqm-v1.5:{result.inputs.data_date}",
        formula_id="qqqm_three_scenario_cashflow_v1_5",
        model_version="1.5",
        return_label="IRR",
        value_label="公允价值",
        warnings=tuple(item for item in (warning, result.warning) if item),
    )
