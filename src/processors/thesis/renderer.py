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
from src.processors.event_semantics import event_pattern, normalize_event_text
from src.processors.html_safe import is_safe_url
from src.utils.news_facts import canonical_fact

from .extractor import (
    _normalize_grounding_url,
    _value,
    _verified_grounding_row,
)

logger = logging.getLogger(__name__)
_VERSION = 13
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
    sector: bool = False

    def matches(self, text: str) -> bool:
        from src.processors.news_selection import reporting_text

        # A masthead and separate background sentence cannot supply the action.
        return any(re.search(self.subject, clause, re.I) and re.search(self.action, normalize_event_text(clause), re.I)
                   for clause in re.split(r"[。；;!?]|(?<!\bInc)\.(?=\s+[A-Z])|\b(?:while|whereas)\b", reporting_text(text), flags=re.I))


# Matching only chooses a question to monitor. It cannot promote a plan to an
# order, a launch to commercial success, or an application to regulatory approval.
_RULES = (
    WatchRule(
        'portfolio-allocation',
        r'\b(?:stocks?|shares?|stakes?|holdings?)\b|股票|股份|持股',
        r'\b(?:buys?|bought|purchas\w*|owns?|holds?|sells?|sold)\b|买入|增持|持有|减持|卖出',
        '证券投资的长期回报需由持仓与资本配置验证',
        '持仓比例、投入金额、后续增减持与资本回报。',
    ),
    WatchRule(
        "product-data-governance",
        r"\b(?:privacy|permissions?|data access|access (?:to )?(?:(?:their|system|personal|user)\s+){0,3}data)\b|隐私|权限|数据访问|访问.{0,8}数据",
        r"\b(?:updates?|updated|changes?|changed|restricts?|restricted|requires?|notify|notifies|alerts?)\b|更新|修改|调整|限制|要求|通知|提醒",
        "产品的数据治理变化需观察实际采用与执行效果",
        "适用范围、用户与开发者采用、执行效果及披露的合规成本。",
    ),
    WatchRule(
        "product-reliability",
        r"\b(?:iphone|devices?|software|services?|products?)\b|手机|设备|软件|服务|产品",
        r"\b(?:replac(?:e|es|ed|ing|ements?)|recalls?|outages?|defects?|lost service|lose service|service.loss)\b|更换|替换|召回|故障|中断|失去服务",
        "产品可靠性事件的长期影响需由范围与处置结果验证",
        "受影响范围、修复进度、处置成本与后续客户反馈。",
    ),
    WatchRule(
        "infrastructure-investment",
        r"\b(?:cloud|data cent(?:er|re)s?|infrastructure|fabs?)\b|云|数据中心|基础设施|晶圆厂|产能",
        event_pattern("investment") + r"|\b(?:build(?:s|ing)?|expand(?:s|ed|ing)?|spend(?:s|ing)?|under construction|being built)\b|投入|支出|开支|建设|扩建|扩产|在建",
        "基础设施投入的长期价值取决于资本回报",
        "实际投入、投产进度、利用率与现金流能否匹配。",
    ),
    WatchRule(
        "license-economics",
        r"\b(?:licen[cs](?:e|es|ing)|patents?)\b|专利|许可|授权",
        r"\b(?:renew(?:s|ed|ing|al)?|agree(?:s|d|ment|ments)?|sign(?:s|ed|ing)?|expir(?:e|es|ed|ing|ation|y)|terminat(?:e|es|ed|ing|ion))\b|续签|协议|签署|到期|终止",
        "专利授权的长期收益取决于合同持续性与收费安排",
        "合同期限、授权范围、收费安排与续约情况。",
    ),
    WatchRule(
        "payment-commercialization",
        r"\b(?:settlements?|payments?)\b|结算|支付",
        event_pattern("launch") + r"|\b(?:enabl(?:e|es|ed|ing)|adopt(?:s|ed|ing|ion)?)\b|采用",
        "支付新业务的长期价值仍需真实交易规模验证",
        "实际交易量、客户采用、费用收入与合规成本。",
    ),
    WatchRule(
        "product-release-risk",
        r"\bGPT[- .]?\d+(?:\.\d+)?\b|\b(?:models?|platforms?|iphone|devices?|chips?)\b|模型|平台|手机|设备|芯片",
        event_pattern("pause", "cancel", "delay"),
        "产品发布调整后的长期影响取决于后续安排",
        "调整原因、问题解决进度、后续发布安排与投入变化。",
    ),
    WatchRule(
        "product-commercialization",
        r"\bGPT[- .]?\d+(?:\.\d+)?\b|\b(?:models?|platforms?|iphone|devices?|chips?)\b|模型|平台|手机|设备|芯片",
        event_pattern("launch") + r"|上市",
        "新产品的长期价值仍需持续采用和盈利兑现",
        "用户采用、收入贡献、利润率与持续投入。",
    ),
    WatchRule(
        "operating-performance",
        r"\b(?:revenues?|margins?|cash flow|backlogs?)\b|营收|收入|利润率|现金流|在手订单",
        r"\d|增长|下降|上调|下调|增加|减少",
        "经营质量需要利润与现金流共同验证",
        "后续财报中的增长持续性、利润率与现金流。",
    ),
    WatchRule(
        "capital-allocation",
        r"\b(?:buybacks?|repurchas(?:e|es|ed|ing)|dividends?|acquir(?:e|es|ed|ing)|acquisitions?)\b|回购|股息|分红|收购",
        event_pattern("announcement", "approval", "completion", "cancel") + r"|\bagree(?:s|d|ment)?\b|\bplan(?:s|ned|ning)?\b|协议|计划|取消",
        "资本配置的长期成效应由每股现金回报检验",
        "实际执行金额、资金来源与后续现金回报。",
    ),
    WatchRule(
        "regulatory-access",
        r"\b(?:regulat(?:or|ors|ory|ion|ions)|antitrust|licen[cs](?:e|es|ing))\b|监管|反垄断|牌照",
        event_pattern("approval") + r"|\breject(?:s|ed|ing|ion)?\b|\bbans?\b|\bfine[ds]?\b|驳回|禁令|禁止|罚款",
        "监管事项的长期影响取决于适用范围与执行条件",
        "决定的适用范围、生效条件、后续程序与披露的经营影响。",
    ),
    WatchRule(
        'resource-procurement',
        r'\b(?:energy|electricity|power|nuclear|fuel|raw materials?)\b|能源|电力|核能|燃料|原材料',
        r'\b(?:buys?|buying|purchas(?:e|es|ed|ing)|procur(?:e|es|ed|ing|ement)|suppl(?:y|ies|ied|ying)|sourcing|agreements?|deals?|contracts?)\b|购买|采购|供应|协议|合同',
        '长期资源采购的价值取决于供应稳定性与成本兑现',
        '协议是否落地、供应期限、实际价格与对经营成本的影响。',
    ),
    WatchRule(
        'commercial-distribution',
        r'\b(?:resellers?|distribution|channels?)\b|经销商|分销|销售渠道',
        r'\b(?:sign(?:s|ed|ing)?|inks?|agreements?|deals?|expand(?:s|ed|ing)?|creat(?:e|es|ed|ing))\b|签署|协议|扩展|开辟',
        '商业渠道合作的长期价值需要客户采用与收入验证',
        '合作范围、客户采用、实际交易与收入分成。',
    ),
    WatchRule(
        'strategic-negotiation',
        r'\b(?:chips?|semiconductors?|manufacturing|factor(?:y|ies)|capacity|housing|homebuilders?)\b|芯片|半导体|制造|工厂|产能|住房|住宅',
        r'\b(?:talks?|negotiat\w*|invest\w*|acquir\w*|owns?)\b|谈判|磋商|投资|收购|持有',
        '战略合作与资本投入仍需后续执行验证',
        '谈判是否形成协议、实际投入、合作边界与资本回报。',
    ),
    WatchRule(
        'financial-consolidation',
        r'\b(?:banks?|lenders?|financial system)\b|银行|贷款机构|金融体系',
        r'\b(?:consolidat\w*|merg\w*|shutter\w*|shuts?|closed?)\b|整合|合并|关闭',
        '金融机构整合的长期影响取决于资本与风险处置',
        '整合范围、资本充足率、风险资产处置与信贷供给。',
        sector=True,
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


def _without_background_context(text: str) -> str:
    clauses = re.split(r"[。；;!?]|(?<!\bInc)\.(?=\s+[A-Z])", text, flags=re.I)
    return ";".join(re.split(
        r"此前|先前|以前|曾经|\b(?:previously|historically|formerly)\b",
        clause, maxsplit=1, flags=re.I,
    )[0] for clause in clauses)


def _rule_for(row: dict) -> WatchRule | None:
    original, text = row["excerpt"], row["output_text"]
    if row.get('presentation_company'):
        from src.processors.presentation_vocabulary import COMPANY_DISPLAY_NAMES
        text = COMPANY_DISPLAY_NAMES.get(row['presentation_company'], '') + text
    if editorial_issue(original) or editorial_issue(text):
        return None
    if not re.search(r"[一-鿿]", text):
        return None
    # Denials of a cancellation must not be labelled as a release setback.
    negated_change = r"(?:not|never|no longer)\s+(?:\w+\s+){0,2}(?:scrap|cancel|abandon|delay|shelv)|(?:并未|没有|不会|未)(?:放弃|取消|搁置|暂停|推迟)"
    no_construction = r"not (?:currently )?(?:under construction|being built)|并非在建|没有在建|尚未(?:开工|建设)"
    return next((rule for rule in _RULES if rule.matches(original) and rule.matches(text)
                 and (rule.key != "operating-performance" or (
                     rule.matches(_without_background_context(original))
                     and rule.matches(_without_background_context(text))))
                 and not (rule.key == 'portfolio-allocation' and re.search(r'\brepurchas\w*|\bbuybacks?\b|\bbuys? back\b|回购', original + ' ' + text, re.I))
                 and ((_ENTITY.search(original) and _ENTITY.search(text))
                      or (rule.sector and (row.get('macro_event') or {}).get('geography')))
                 and not (rule.key == "infrastructure-investment" and (
                     re.search(no_construction, original, re.I) or re.search(no_construction, text)))
                 and not (rule.key == "product-release-risk" and (
                     re.search(r"training|evaluation|inference|训练|评估|推理", original + " " + text, re.I) or
                     re.search(negated_change, original, re.I) or re.search(negated_change, text)))), None)


def _named_product(title):
    from src.processors.translation_guard import _MONTH
    pattern = r"\b[A-Z][A-Za-z]+[- ]\d+(?:\.\d+)?(?: [A-Z][a-z]+)?\b"
    return next((m for m in re.finditer(pattern, title)
                 if not re.match(r"(?:" + _MONTH + r"|Q[1-4]|FY|Fiscal|Year|Quarter|Up|Down|Higher|Lower|Revenue|Sales|Profit|Margin|Headcount)\b", m[0], re.I)
                 and not re.match(r'\s*(?:%|percent\b|basis points?\b|bps\b|million\b|billion\b|dollars?\b)', title[m.end():], re.I)), None)


def _event_subject(row):
    from src.processors.presentation_vocabulary import COMPANY_DISPLAY_NAMES
    entity = _ENTITY.search(row['excerpt'])
    name = COMPANY_DISPLAY_NAMES.get(row.get('presentation_company'), entity[0] if entity else '')
    product = _named_product(row.get('original_title', ''))
    return name + (' ' + product[0] if product else '')


def _event_key(row: dict, theme: str) -> str:
    """Group one named product event, never all events of a company/theme."""
    from src.processors.translation_guard import _quantities

    title = row.get('original_title', '')
    entity = _ENTITY.search(title)
    product = _named_product(title)
    if theme != 'product-commercialization' or not entity or not product:
        return _fact_key(row)
    numbers = sorted(str(key) for key in set(_quantities(title)) | set(_quantities(row['excerpt'])))
    identity = [theme, row.get('presentation_company') or entity[0].casefold(), product[0].casefold(), str(row.get('published_at', ''))[:10], numbers]
    return hashlib.sha256(str(identity).encode()).hexdigest()


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
    from types import SimpleNamespace

    from src.processors.news_selection import explicit_old_event

    source = SimpleNamespace(published_at=row.get("published_at"))
    if explicit_old_event(source, row.get("excerpt", ""), allow_fresh_update=False):
        return None, "old_event_not_new_evidence"
    rule = _rule_for(row)
    if not rule:
        return None, "no_bounded_long_term_watchpoint"
    # A forward estimate is useful, but cannot become realised operating evidence.
    forecast = bool(re.search(r"\b(?:forecasts?|estimates?|projects?|expects?|could|may)\b|预测|预计|估计|可能", row['excerpt'] + ' ' + row['output_text'], re.I))
    key = _fact_key(row)
    item = {
        "theme": rule.key,
        "thesis": "收入预期仍需业务兑现与利润贡献验证" if forecast and rule.key == "operating-performance" else rule.title,
        "subject": _event_subject(row) or ('金融机构整合' if rule.sector else ''),
        "event_key": _event_key(row, rule.key),
        "evidence_type": "forecast" if forecast else "reported_event",
        "marker": "预期变化" if forecast else (
            "新变量"
            if rule.key
            in {"payment-commercialization", "product-commercialization", "product-release-risk", "regulatory-access"}
            else "新证据"
        ),
        "updated": True,
        "fact": row["output_text"],
        "watch": "后续披露的实际业务收入、利润贡献与相关资本投入是否兑现本次预期。" if forecast and rule.key == "operating-performance" else rule.watch,
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
    seen_events = set()
    for section, row in _verified_rows(sources or {}):
        key = _fact_key(row)
        item, reason = _publication_item(section, row, today)
        if not reason:
            for published_key in (key, item['event_key']):
                try:
                    if published_key in history and 0 <= (today - date.fromisoformat(history[published_key])).days <= _HISTORY_DAYS:
                        reason = "already_published_fact"
                except (ValueError, TypeError):
                    pass
        if not reason and key in seen:
            reason = "duplicate_fact"
        if not reason and item["event_key"] in seen_events:
            reason = "duplicate_event"
        if not reason:
            seen.add(key)
        if not reason and len(items) >= 3:
            reason = "edition_limit"
        decisions.append({"fact_key": key, "url": row["url"], "reason": reason or "selected"})
        if reason:
            continue
        items.append(item)
        seen_events.add(item["event_key"])
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
    seen_events = set()
    for item in original:
        if (
            isinstance(item, dict)
            and item in allowed
            and is_safe_url(item.get("url", ""))
            and item["fact_key"] not in seen
            and item["event_key"] not in seen_events
            and len(items) < 3
        ):
            items.append(item)
            seen.add(item["fact_key"])
            seen_events.add(item["event_key"])
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
    kept.update({item.get("event_key", item["fact_key"]): today.isoformat() for item in section.items})
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / "thesis_publications.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
