"""Deterministic checks on complete source translations, not semantic proof.

Translations are separately labelled in the audit. These checks reject changed
quantities, currencies, entities, modality and selected material event states.
They never authorize arbitrary summaries or use an LLM to approve its own text.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from decimal import Decimal

# Match negative phrases before checking event states (e.g. not approved).
_NEGATION = r"\b(?:not|never|no|without|denies?|denied|cannot|can't|won't|hasn't|isn't|didn't|unapproved)\b|尚未|并未|没有|未获|未被|未能|不曾|否认|无法|不能|不会|不予|不批准|未经"
_MODALITY = r"\b(?:may(?!\s+\d)|might|could|would|plans?|planned|planning|proposes?|proposed|proposal|expects?|expected|aims?|seeks?|seeking|considering|reportedly|rumou?rs?|consensus|pending|awaiting|will|shall|intends?|scheduled)\b|将(?=于|在|会|要|发布|推出|收购|投资|任命|启动|发射|出任|担任|生效)|可能|或将|拟|计划|预计|预期|提议|考虑|据传|传闻|寻求|等待|待定|待批|尚待"
_EVENTS = {
    "approval": r"\b(?:approv\w*|clearance|greenlight\w*)\b|批准|获批|监管放行",
    "completion": r"\b(?:completed?|finalized?|closed the deal)\b|完成|已交割|已落地",
    "cut": r"\b(?:cuts?|cutting|trims?|trimmed|lowers?|lowered|reduces?|reduced|reduction)\b|下调|削减|降息|减少|降低|减产|裁减|裁员",
    "raise": r"\b(?:raises?|raised|lifts?|lifted|upgrades?|upgraded|hikes?|hiked|increases?|increased|boosts?|boosted|expands?|expanded|expansion|growth|grew|grow\w*)\b|上调|加息|增加|提高|扩大|扩张|增长|扩建",
    "hold": r"\b(?:holds?|unchanged|maintains?|maintained)\b|维持|不变|保持|持平",
    "fall": r"\b(?:falls?|fell|declines?|declined|drops?|dropped|slumps?|slumped)\b|下降|下跌|回落|下滑",
    "rise": r"\b(?:rises?|rose|gains?|gained|surges?|surged|rallies|rallied|is up)\b|上升|上涨|攀升|飙升",
    "profit": r"\b(?:profits?|earnings|net income)\b|盈利|利润|收益(?!率)",
    "loss": r"\b(?:loss|losses)\b|亏损",
    "revenue": r"\b(?:revenues?|sales)\b|营收|收入|销售",
    "investment": r"\b(?:invest\w*|capex|capital spending)\b|投资|资本开支|资本支出",
    "acquisition": r"\b(?:acqui\w*|merger|takeover|buyout)\b|收购|并购|合并",
    "launch": r"\b(?:launch\w*|rolls? out|rollout|switched on|starts?|releases?|released|unveils?|unveiled)\b|发布|推出|亮相|发射|启用|启动|开通",
}
_ENTITIES = {
    "Microsoft": ("Microsoft", "微软"), "Google": ("Google", "谷歌"),
    "Alphabet": ("Alphabet",), "Apple": ("Apple", "苹果"),
    "Nvidia": ("Nvidia", "英伟达"), "OpenAI": ("OpenAI",),
    "Anthropic": ("Anthropic",), "Amazon": ("Amazon", "亚马逊"),
    "Meta": ("Meta",), "Tesla": ("Tesla", "特斯拉"),
    "Fed": ("Fed", "Federal Reserve", "美联储"),
    "UN": ("UN", "联合国"), "TPU": ("TPU",), "OS": ("OS", "操作系统"), "IP": ("IP", "知识产权"),
    "ECB": ("ECB", "European Central Bank", "欧洲央行"),
    "Buffett": ("Buffett", "巴菲特"), "Huang": ("Huang", "黄仁勋"),
    "Altman": ("Altman", "奥特曼"), "Nadella": ("Nadella", "纳德拉"),
    "TSMC": ("TSMC", "Taiwan Semiconductor", "台积电"),
    "Tencent": ("Tencent", "腾讯"), "Berkshire": ("Berkshire", "伯克希尔"),
}
_SCALE = {"thousand": 1000, "million": 10**6, "billion": 10**9,
          "trillion": 10**12, "b": 10**9, "bn": 10**9, "m": 10**6, "mn": 10**6, "千": 1000, "万": 10**4, "亿": 10**8, "万亿": 10**12}
_NUMBER = re.compile(r"(?P<n>[+-]?\d+(?:,\d{3})*(?:\.\d+)?)\s*(?P<scale>trillion|billion|million|thousand|bn\b|mn\b|b\b|m\b|万亿|亿|万|千)?\s*(?P<unit>%|percent(?:age points?)?|basis points?|bps?|基点|个百分点|美元|港元|欧元|dollars?|USD|HKD|EUR)?", re.I)


def _quantities(text: str) -> Counter:
    result = Counter()
    months = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
              "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
    def english_date(match):
        result[("date", months[match[1][:3].lower()], int(match[2]), match[3] or "")] += 1
        return " "
    def chinese_date(match):
        result[("date", int(match[2]), int(match[3]), match[1] or "")] += 1
        return " "
    text = re.sub(r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+(\d{1,2})(?:,?\s+(\d{4}))?\b", english_date, text, flags=re.I)
    text = re.sub(r"(?:(\d{4})\s*年)?\s*(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]", chinese_date, text)
    for match in _NUMBER.finditer(text):
        value = Decimal(match['n'].replace(',', '')) * _SCALE.get((match['scale'] or '').lower(), 1)
        unit = (match['unit'] or '').lower()
        if unit in ('%', 'percent'):
            unit = 'percent'
        elif unit in ('percentage point', 'percentage points', '个百分点'):
            unit = 'percentage_points'
        elif unit in ('basis point', 'basis points', 'bp', 'bps', '基点'):
            unit = 'basis_points'
        else:
            for canonical, aliases in {'USD': ('美元', 'dollar', 'dollars', 'usd'), 'HKD': ('港元', 'hkd'), 'EUR': ('欧元', 'eur')}.items():
                if unit in aliases:
                    unit = canonical
        prefix = text[max(0, match.start() - 3):match.start()]
        if prefix.endswith('HK$'):
            unit = 'HKD'
        elif prefix.endswith('$'):
            unit = 'USD'
        elif prefix.endswith('€'):
            unit = 'EUR'
        result[(value, unit)] += 1
    return result


def _contains(text: str, alias: str) -> bool:
    pattern = re.escape(alias)
    if alias.isascii():
        pattern = rf"(?<![A-Za-z0-9_]){pattern}(?![A-Za-z0-9_])"
    return bool(re.search(pattern, text, re.I))


def _entity_order(text: str) -> list[str]:
    positions = []
    for entity, aliases in _ENTITIES.items():
        hits = []
        for alias in aliases:
            pattern = re.escape(alias)
            if alias.isascii():
                pattern = rf"(?<![A-Za-z0-9_]){pattern}(?![A-Za-z0-9_])"
            hits.extend(m.start() for m in re.finditer(pattern, text, re.I))
        if hits:
            positions.append((min(hits), entity))
    return [entity for _, entity in sorted(positions)]


def _scoped_states(text: str) -> set[tuple]:
    states = set()
    # Bind polarity/modality to the event clause so a 'not' elsewhere cannot
    # bless a reversed approval/completion assertion.
    for clause in re.split(r"[，,；;。!?]|\bbut\b|但是|但", text, flags=re.I):
        for event in ("approval", "completion", "acquisition", "launch"):
            if re.search(_EVENTS[event], clause, re.I):
                states.add((event, bool(re.search(_NEGATION, clause, re.I)),
                            bool(re.search(_MODALITY, clause, re.I))))
    return states


def translation_errors(original: str, translated: str) -> list[str]:
    original = unicodedata.normalize('NFKC', original)
    translated = unicodedata.normalize('NFKC', translated)
    errors = []
    if not re.search(r'[一-鿿]', translated):
        errors.append('not_chinese')
    if _quantities(original) != _quantities(translated):
        errors.append('quantities_or_units')
    for name, pattern in {'negation': _NEGATION, 'modality': _MODALITY, **_EVENTS}.items():
        if bool(re.search(pattern, original, re.I)) != bool(re.search(pattern, translated, re.I)):
            errors.append(name)
    for entity, aliases in _ENTITIES.items():
        if any(_contains(original, alias) for alias in aliases) != any(_contains(translated, alias) for alias in aliases):
            errors.append('entity:' + entity)
    if _entity_order(original) != _entity_order(translated):
        errors.append('entity_order')
    if _scoped_states(original) != _scoped_states(translated):
        errors.append('event_scope')
    # Preserve identifiers, model names and acronyms literally (including unknown ones).
    identifiers = set(re.findall(r'\b(?:[A-Z]{2,}[A-Z0-9-]*|[A-Za-z]+[-.]\d[\w.-]*)\b', original + ' ' + translated))
    for identifier in identifiers - {'USD', 'HKD', 'EUR', 'CEO', 'US', 'UK', 'OS', 'UN', 'IP'}:
        if _contains(original, identifier) != _contains(translated, identifier):
            errors.append('identifier:' + identifier)
    if len(translated) > max(100, len(original) * 2):
        errors.append('expanded_text')
    return errors
