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
_NEGATION = r"\b(?:not|never|no|without|denies?|denied|cannot|can't|won't|hasn't|isn't|didn't|unapproved)\b|尚未|并未|没有|未获|未被|未能|不曾|否认|无法|不能|不会|不予|不批准|未经|而非|并非|不是"
_MODALITY = r"\b(?:may(?!\s+\d)|might|could|would|plans?|planned|planning|proposes?|proposed|proposal|expects?|expected|aims?|seeks?|seeking|considering|reportedly|rumou?rs?|consensus|pending|awaiting|will|shall|intends?|scheduled|looms?)\b|\bto\s+(?:pay|invest|acquire|launch|release|appoint)\b|即将|将(?=上市|支付|于|在|会|要|发布|推出|收购|投资|任命|启动|发射|出任|担任|生效)|可能|或将|拟|计划|预计|预期|提议|考虑|据传|传闻|寻求|等待|待定|待批|尚待"
_EVENTS = {
    "approval": r"\b(?:approv\w*|clearance|greenlight\w*)\b|批准|获批|监管放行",
    "completion": r"\b(?:completed?|finalized?|closed the deal)\b|完成|已交割|已落地",
    "cut": r"\b(?:cuts?(?!\s+(?:[A-Za-z.]+\s+){0,4}off\b)|cutting(?!\s+(?:[A-Za-z.]+\s+){0,4}off\b)|trims?|trimmed|lowers?|lowered|reduces?|reduced|reduction)\b|下调|削减|降息|减少|降低|减产|裁减|裁员",
    "cut_off": r"\bcut(?:s|ting)?\s+(?:[A-Za-z.]+\s+){0,4}off\b|切断|隔绝|孤立",
    "payment": r"\b(?:pay|pays|paid|paying)\b|支付|付给",
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
# Direction synonyms are one fact, not separate events that a translation must
# repeat twice ("revenue rose" and "revenue increased" both mean 营收增长).
_EVENTS["raise"] += "|" + _EVENTS.pop("rise") + r"|\bexpanding\b"
_EVENTS["cut"] += r"|\blayoffs?\b|走低"
_EVENTS["launch"] += r"|\bintroduc(?:e|es|ed|ing)\b|\bgoes live\b|上线"
_EVENTS["payment"] += r"|\bpayments?\b|\bpayable\b"
_EVENTS["payment_completed"] = r"\b(?:has|have|had|already)\s+paid\b|已(?:经)?支付|已付"
_EVENTS["payment_order"] = r"\b(?:orders?|ordered|requires?|required)\b.*\bpay\b|命令.*支付|责令.*支付|判令.*支付|判赔"
_EVENTS["person_release"] = r"\bperson_release\b|释放"
_EVENTS["pause"] = r"\b(?:paus\w*|suspend\w*|halt\w*)\b|暂停|中止"
_EVENTS["cancel"] = r"\b(?:cancel\w*|scrap\w*|abandon\w*|shelv\w*)\b|取消|放弃|搁置"
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
    "New York": ("New York", "纽约"),
}
_SCALE = {"thousand": 1000, "million": 10**6, "billion": 10**9,
          "trillion": 10**12, "b": 10**9, "bn": 10**9, "m": 10**6, "mn": 10**6, "千": 1000, "万": 10**4, "亿": 10**8, "万亿": 10**12}
_NUMBER = re.compile(r"(?P<n>[+-]?\d+(?:,\d{3})*(?:\.\d+)?)\s*(?P<scale>trillion|billion|million|thousand|bn\b|mn\b|b\b|m\b|万亿|亿|万|千)?(?:\s*-\s*|\s*)(?P<unit>%|percent(?:age points?)?|basis points?|bps?|基点|个百分点|美元|港元|欧元|dollars?|USD|HKD|EUR|years?|months?|days?|weeks?|decades?|年|个月|天|周)?", re.I)


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
    text = re.sub(r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+(\d{1,2})(?:,?\s+(\d{4}))?\b", english_date, text, flags=re.I)
    text = re.sub(r"(?:(\d{4})\s*年)?\s*(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]", chinese_date, text)
    # Standalone calendar years match English "by 2030"; full dates were handled above.
    text = re.sub(r"(?<!\d)([12]\d{3})\s*年", r"\1", text)
    # Normalize written durations only, never arbitrary words or company names.
    en_counts = dict(zip(["one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve"], range(1, 13), strict=True))
    zh_counts = dict(zip(["一", "二", "三", "四", "五", "六", "七", "八", "九", "十", "十一", "十二"], range(1, 13), strict=True))
    text = re.sub(r"\b(" + "|".join(en_counts) + r")(?=\s+(?:years?|months?|days?|weeks?|decades?)\b)", lambda m: str(en_counts[m[0].lower()]), text, flags=re.I)
    ordinals = {'first': 1, 'second': 2, 'third': 3, 'fourth': 4, 'fifth': 5}
    text = re.sub(r"\b(" + "|".join(ordinals) + r")(?=\s+(?:days?|weeks?)\b)",
                  lambda m: str(ordinals[m[0].lower()]), text, flags=re.I)
    text = re.sub(r"\ba(?=\s+(?:day|week|month|year|decade)\b)", "1", text, flags=re.I)
    def chinese_count(match):
        raw = match[0]
        if "十" in raw:
            tens, ones = raw.split("十")
            return str((zh_counts[tens] if tens else 1) * 10 + (zh_counts[ones] if ones else 0))
        return str(zh_counts[raw])
    text = re.sub(r"[一二三四五六七八九]?十[一二三四五六七八九]?(?=年|个月|天|周)|[一二三四五六七八九](?=年|个月|天|周)", chinese_count, text)
    for match in _NUMBER.finditer(text):
        value = Decimal(match['n'].replace(',', '')) * _SCALE.get((match['scale'] or '').lower(), 1)
        unit = (match['unit'] or '').lower()
        if unit in ('decade', 'decades'):
            unit = 'years'
            value *= 10
        elif unit in ('year', 'years', '年'):
            unit = 'years'
        elif unit in ('month', 'months', '个月'):
            unit = 'months'
        elif unit in ('week', 'weeks', '周'):
            unit = 'weeks'
        elif unit in ('day', 'days', '天'):
            unit = 'days'
        elif unit in ('%', 'percent'):
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
        for event in ("approval", "completion", "acquisition", "launch", "payment", "person_release", "pause", "cancel"):
            if re.search(_EVENTS[event], clause, re.I):
                states.add((event, bool(re.search(_NEGATION, clause, re.I)),
                            bool(re.search(_MODALITY, clause, re.I))))
    return states


def _financial_bindings(text: str) -> dict:
    """Keep revenue and EPS figures attached to their own clauses, not a number bag."""
    bindings = {}
    for clause in re.split(r'[，；;。]|(?<!\d),(?!\d)|\bwhile\b|\bwhereas\b|而', text, flags=re.I):
        metrics = [name for name, pattern in {
            'revenue': r'\brevenues?\b|营收|收入', 'eps': r'\bEPS\b|每股收益',
        }.items() if re.search(pattern, clause, re.I)]
        for metric in metrics:
            bindings.setdefault(metric, []).append((
                _quantities(clause),
                bool(re.search(_EVENTS['raise'], clause, re.I)),
                bool(re.search(_EVENTS['cut'] + '|' + _EVENTS['fall'], clause, re.I)),
            ))
    return bindings


def translation_errors(original: str, translated: str) -> list[str]:
    original = unicodedata.normalize('NFKC', original)
    translated = unicodedata.normalize('NFKC', translated)
    # Release of people is not a product launch. Preserve the rest of the clause,
    # including negation, numbers and entities, for the checks below.
    original = re.sub(r"\breleas(?:e|es|ed|ing)(?=\s+(?:(?:the|a|two|three|\d+)\s+)?(?:suspects?|prisoners?|hostages?)\b)",
                      "person_release", original, flags=re.I)
    # A court ordering somebody to pay is an obligation, not a tentative plan.
    original = re.sub(r"\b(orders?|ordered|requires?|required)(\s+[^.;!?]{1,80}?)\bto\s+(pay)\b",
                      r"\1\2\3", original, flags=re.I)
    errors = []
    # A calendar period does not physically approach a financial asset. Retry
    # this literal headline construction instead of rewriting checked evidence.
    if (re.search(r"\blooms?\b", original, re.I) and
            re.search(r"(?:月|季度|年底|年末)\s*(?:逼近|迫近).{0,20}(?:国债|美债|债券|股票|股市|Treasuries|bonds|stocks)", translated, re.I)):
        errors.append("calendar_market_word_order")
    from src.processors.technical_context import AI_METHODS
    if re.search(r'AI|artificial intelligence|model|人工智能|模型', original + translated, re.I):
        for method, pattern in AI_METHODS.items():
            if bool(re.search(pattern, original, re.I)) != bool(re.search(pattern, translated, re.I)):
                errors.append('technical_method:' + method)
    if re.search(r"safety gaps?", original, re.I) and re.search(r"安全漏洞", translated):
        errors.append("safety_gap_not_vulnerability")
    if re.search(r"(?:in |a )blow to", original, re.I) and re.search(r"(?:^|[，,；;])\s*打击", translated):
        errors.append("economic_impact_not_attack")
    # Preserve the object of a pause/cancellation, not merely the verb/model.
    from src.processors.editorial_evidence import model_status_claim
    if model_status_claim(original) or model_status_claim(translated):
        for scope, pattern in {
            "training": r"\btraining\b|训练",
            "evaluation": r"\bevaluat\w*\b|评估|评测",
            "tool_use": r"tool[- ]use|using tools|工具使用|使用工具",
            "inference": r"\binference\b|推理",
            "release": r"\b(?:release|launch|rollout|deployment)\b|发布|推出|上线|部署",
            "service": r"\b(?:service|API|subscription)\w*\b|服务|API|订阅",
        }.items():
            if bool(re.search(pattern, original, re.I)) != bool(re.search(pattern, translated, re.I)):
                errors.append("status_scope:" + scope)
    if (re.search(r'\blegal (?:costs?|fees?|bills? (?:stack up|mount|pile up))\b', original, re.I)
            and re.search(r'法案|议案|法律草案', translated)):
        errors.append('legal_cost_sense')
    if not re.search(r'[一-鿿]', translated):
        errors.append('not_chinese')
    if _quantities(original) != _quantities(translated):
        errors.append('quantities_or_units')
    bindings = _financial_bindings(original)
    if len(bindings) > 1 and bindings != _financial_bindings(translated):
        errors.append('financial_metric_binding')
    for name, pattern in {'negation': _NEGATION, 'modality': _MODALITY, **_EVENTS}.items():
        source_present = bool(re.search(pattern, original, re.I))
        translated_present = bool(re.search(pattern, translated, re.I))
        if name == 'modality' and re.search(r'\blooms?\b', original, re.I):
            translated_present |= bool(re.search(r'临近|迫近|逼近|将至|面临|迎来', translated))
        if source_present != translated_present:
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
    location_tokens = {'NEW', 'YORK'} if _contains(original, 'New York') else set()
    for identifier in identifiers - {'USD', 'HKD', 'EUR', 'CEO', 'US', 'UK', 'OS', 'UN', 'IP'} - location_tokens:
        if _contains(original, identifier) != _contains(translated, identifier):
            errors.append('identifier:' + identifier)
    if len(translated) > max(100, len(original) * 2):
        errors.append('expanded_text')
    return errors
