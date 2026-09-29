"""Publish bounded, source-bound long-term watchpoints, never free-form theses.

The old research ledger is deliberately not a publication source. Every displayed
fact must still exist in a visible news block, with its verified original mapping.
Interpretation is limited to code-owned monitoring questions, not model assertions.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from src.processors.editorial_evidence import analysis_source, editorial_issue
from src.processors.html_safe import is_safe_url
from src.utils.news_facts import canonical_fact

from .extractor import (
    _normalize_grounding_url,
    _value,
    _verified_grounding_row,
)

logger = logging.getLogger(__name__)
_VERSION = 2
_HISTORY_DAYS = 90
_SECTION_NAMES = {
    "company_news": "昨日动态",
    "macro": "宏观视野",
    "voices": "关键发言",
    "frontier_labs": "前沿动态",
}
# Whole company names / explicit tickers only: no inference from a country,
# customer, technology or old theme to an unnamed holding's demand.
_ENTITY = re.compile(
    r"\b(?:Microsoft|Apple|Nvidia|Alphabet|Google|TSMC|Berkshire(?: Hathaway)?|Costco|Mastercard|"
    r"Moody['’]s|Coca-Cola|Linde|American Express|Tencent|Pop Mart|OpenAI|Anthropic|"
    r"MSFT|AAPL|NVDA|GOOG|TSM|BRK\.B|COST|MCO|MA|KO|LIN|AXP)\b|"
    r"微软|苹果|英伟达|谷歌|台[积積]电|伯克希尔|好市多|万事达|穆迪|可口可乐|林德|美国运通|腾讯|泡泡玛特",
    re.I,
)


@dataclass(frozen=True)
class WatchRule:
    key: str
    subject: str
    action: str
    title: str
    watch: str

    def matches(self, text: str) -> bool:
        return bool(re.search(self.subject, text, re.I) and re.search(self.action, text, re.I))


# Matching only chooses a question to monitor. It cannot promote a plan to an
# order, a launch to commercial success, or an application to regulatory approval.
_RULES = (
    WatchRule(
        "infrastructure-investment",
        r"cloud|data cent(?:er|re)s?|infrastructure|fab\b|云|数据中心|基础设施|晶圆厂|产能",
        r"invest|capex|capital expend|build|expand|spend|under construction|being built|投资|投入|资本开支|建设|扩建|扩产|在建",
        "基础设施投入的长期价值取决于资本回报",
        "实际投入、投产进度、利用率与现金流能否匹配。",
    ),
    WatchRule(
        "license-economics",
        r"licen[cs]|patent|专利|许可|授权",
        r"renew|agree|sign|expir|terminat|续签|协议|签署|到期|终止",
        "专利授权的长期收益取决于合同持续性与收费安排",
        "合同期限、授权范围、收费安排与续约情况。",
    ),
    WatchRule(
        "payment-commercialization",
        r"settlement|payment|结算|支付",
        r"launch|enable|switch(?:ed)? on|roll.?out|introduc|adopt|开通|推出|启用|采用|上线",
        "支付新业务的长期价值仍需真实交易规模验证",
        "实际交易量、客户采用、费用收入与合规成本。",
    ),
    WatchRule(
        "product-release-risk",
        r"\bGPT[- .]?\d+(?:\.\d+)?\b|model|platform|iphone|device|chip|模型|平台|手机|设备|芯片",
        r"scrap|abandon|cancel|shelv|axes?|halt|delay|postpon|放弃|取消|搁置|砍掉|暂停|推迟|延后",
        "产品发布调整后的长期影响取决于后续安排",
        "调整原因、问题解决进度、后续发布安排与投入变化。",
    ),
    WatchRule(
        "product-commercialization",
        r"\bGPT[- .]?\d+(?:\.\d+)?\b|model|platform|iphone|device|chip|模型|平台|手机|设备|芯片",
        r"launch|releas|introduc|roll.?out|推出|发布|上市|上线",
        "新产品的长期价值仍需持续采用和盈利兑现",
        "用户采用、收入贡献、利润率与持续投入。",
    ),
    WatchRule(
        "operating-performance",
        r"revenue|margin|cash flow|backlog|营收|收入|利润率|现金流|在手订单",
        r"\d|增长|下降|上调|下调|增加|减少",
        "经营质量需要利润与现金流共同验证",
        "后续财报中的增长持续性、利润率与现金流。",
    ),
    WatchRule(
        "capital-allocation",
        r"buyback|repurchas|dividend|acqui[rs]|回购|股息|分红|收购",
        r"announc|approv|agree|complet|plan|cancel|宣布|批准|协议|完成|计划|取消",
        "资本配置的长期成效应由每股现金回报检验",
        "实际执行金额、资金来源与后续现金回报。",
    ),
    WatchRule(
        "regulatory-access",
        r"regulat|antitrust|licen[cs]|监管|反垄断|牌照",
        r"approv|reject|ban\b|fine[ds]?\b|批准|驳回|禁令|禁止|罚款",
        "监管事项的长期影响取决于适用范围与执行条件",
        "决定的适用范围、生效条件、后续程序与披露的经营影响。",
    ),
)


@dataclass
class JudgmentSection:
    items: list[dict[str, Any]]
    audit: dict[str, Any] | None = None


def publication_sources(
    *, company_news=None, macro_news=None, figure_summaries=None, frontier_labs_events=None
):
    return {
        "company_news": [company_news] if company_news else [],
        "macro": [macro_news] if macro_news else [],
        "voices": [item for group in figure_summaries or [] for item in _value(group, "items", [])],
        "frontier_labs": list(frontier_labs_events or [])[:2],
    }


def _verified_rows(sources: dict):
    for section, objects in sources.items():
        if section not in _SECTION_NAMES:
            continue
        for obj in objects:
            if section in ("company_news", "macro"):
                urls = {
                    _normalize_grounding_url(_value(f, "url")) for f in _value(obj, "footnotes", [])
                }
            else:
                urls = {_normalize_grounding_url(_value(obj, "source_url"))}
            for row in _value(obj, "evidence", []) or []:
                verified = _verified_grounding_row(obj, row)
                if verified is not None and verified[0] in urls:
                    yield section, row


def _fact_key(row: dict) -> str:
    # Independent of URL, translation wording, edition and the old theme label.
    return hashlib.sha256(canonical_fact(row["excerpt"]).encode()).hexdigest()


def _rule_for(row: dict) -> WatchRule | None:
    original, text = row["excerpt"], row["output_text"]
    if editorial_issue(original) or editorial_issue(text):
        return None
    if not _ENTITY.search(original) or not _ENTITY.search(text) or not re.search(r"[一-鿿]", text):
        return None
    # Denials of a cancellation must not be labelled as a release setback.
    negated_change = r"(?:not|never|no longer)\s+(?:\w+\s+){0,2}(?:scrap|cancel|abandon|delay|shelv)|(?:并未|没有|不会|未)(?:放弃|取消|搁置|暂停|推迟)"
    no_construction = r"not (?:currently )?(?:under construction|being built)|并非在建|没有在建|尚未(?:开工|建设)"
    return next((rule for rule in _RULES if rule.matches(original) and rule.matches(text)
                 and not (rule.key == "infrastructure-investment" and (
                     re.search(no_construction, original, re.I) or re.search(no_construction, text)))
                 and not (rule.key == "product-release-risk" and (
                     re.search(r"training|evaluation|inference|训练|评估|推理", original + " " + text, re.I) or
                     re.search(negated_change, original, re.I) or re.search(negated_change, text)))), None)


def _publication_item(section: str, row: dict, today: date):
    try:
        observed = datetime.fromisoformat(
            str(row.get("published_at", "")).replace("Z", "+00:00")
        ).date()
    except ValueError:
        return None, "missing_source_date"
    if not 0 <= (today - observed).days <= 7:
        return None, "source_outside_news_window"
    if row.get("source_kind") == "analysis" or analysis_source(
            row.get("original_title", ""), row.get("original_summary", "")):
        return None, "analysis_not_new_evidence"
    rule = _rule_for(row)
    if not rule:
        return None, "no_bounded_long_term_watchpoint"
    key = _fact_key(row)
    item = {
        "theme": rule.key,
        "thesis": rule.title,
        "marker": (
            "新变量"
            if rule.key
            in {"payment-commercialization", "product-commercialization", "product-release-risk", "regulatory-access"}
            else "新证据"
        ),
        "updated": True,
        "fact": row["output_text"],
        "watch": rule.watch,
        "url": row["url"],
        "section": _SECTION_NAMES[section],
        "source_section": section,
        "source_date": row["published_at"],
        "fact_key": key,
        "rule_version": _VERSION,
        "evidence": {
            key: row[key]
            for key in (
                "original_title",
                "original_summary",
                "excerpt",
                "output_text",
                "validated_text",
                "mode",
                "url",
                "published_at",
                "source_name",
                "source_sha256",
                "presentation_version",
            )
            if key in row
        },
    }
    return item, None


def build_judgment_section(
    events=(),
    *,
    state=None,
    evidence_today=None,
    today=None,
    sources=None,
    history=None,
    selection_audit=None,
) -> JudgmentSection | None:
    """Legacy events/state can never supply missing publication evidence.

    Every distinct, eligible fact may appear immediately. No theme-age, LLM-score,
    or 21-day theme cooldown gate; a repeated fact remains suppressed for 90 days.
    """
    today = today or date.today()
    history = history or {}
    items, decisions, seen = [], [], set()
    for section, row in _verified_rows(sources or {}):
        key = _fact_key(row)
        item, reason = _publication_item(section, row, today)
        if not reason and key in history:
            try:
                if 0 <= (today - date.fromisoformat(history[key])).days <= _HISTORY_DAYS:
                    reason = "already_published_fact"
            except (ValueError, TypeError):
                pass
        if not reason and key in seen:
            reason = "duplicate_fact"
        if not reason:
            seen.add(key)
        if not reason and len(items) >= 3:
            reason = "edition_limit"
        decisions.append({"fact_key": key, "url": row["url"], "reason": reason or "selected"})
        if reason:
            continue
        items.append(item)
    audit = {"version": _VERSION, "published": len(items), "decisions": decisions}
    if selection_audit is not None:
        selection_audit.update(audit)
    logger.info("thesis.publication candidates=%d selected=%d", len(decisions), len(items))
    return JudgmentSection(items, audit) if items else None


def validate_publication(section, *, sources, today):
    """Final rendering gate: rederive every visible word and link from actual body sources."""
    if not section:
        return None
    allowed = []
    for source_section, row in _verified_rows(sources):
        item, _ = _publication_item(source_section, row, today)
        if item:
            allowed.append(item)
    original = _value(section, "items", [])
    items, seen = [], set()
    for item in original:
        if (
            isinstance(item, dict)
            and item in allowed
            and is_safe_url(item.get("url", ""))
            and item["fact_key"] not in seen
            and len(items) < 3
        ):
            items.append(item)
            seen.add(item["fact_key"])
    if len(items) != len(original):
        logger.warning("thesis.publication_rejected count=%d", len(original) - len(items))
    return JudgmentSection(items[:3], _value(section, "audit")) if items else None


def load_publications(state_dir: Path) -> dict[str, str]:
    path = state_dir / "thesis_publications.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in data.items()
    ):
        raise ValueError("invalid thesis publication history")
    return data


def commit_publications(section, state_dir: Path, *, today: date) -> None:
    """Only called after SMTP accepts this edition, never for a preview or failed send."""
    if not section:
        return
    history = load_publications(state_dir)
    kept = {}
    for key, value in history.items():
        try:
            if 0 <= (today - date.fromisoformat(value)).days <= _HISTORY_DAYS:
                kept[key] = value
        except ValueError:
            continue
    kept.update({item["fact_key"]: today.isoformat() for item in section.items})
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / "thesis_publications.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
