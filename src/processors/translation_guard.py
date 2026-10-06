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

from src.processors.event_semantics import (
    EVENTS as _EVENTS,
)
from src.processors.event_semantics import (
    MODALITY as _MODALITY,
)
from src.processors.event_semantics import (
    NEGATION as _NEGATION,
)
from src.processors.event_semantics import (
    document_types,
    normalize_event_text,
)
from src.processors.presentation_vocabulary import _LOCALIZED_TERMS_V9

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
    "EU": ("EU", "European Union", "欧盟"),
}
_SCALE = {"thousand": 1000, "million": 10**6, "billion": 10**9,
          "trillion": 10**12, "b": 10**9, "bn": 10**9, "m": 10**6, "mn": 10**6, "千": 1000, "万": 10**4, "亿": 10**8, "万亿": 10**12}
_NUMBER = re.compile(r"(?P<n>[+-]?\d+(?:,\d{3})*(?:\.\d+)?)\s*(?P<scale>trillion|billion|million|thousand|bn\b|mn\b|b\b|m\b|万亿|亿|万|千)?(?:\s*-\s*|\s*)(?P<unit>%|percent(?:age points?)?|basis points?|bps?|基点|个百分点|美元|港元|欧元|dollars?|USD|HKD|EUR|years?|months?|days?|weeks?|decades?|年|个月|天|周)?", re.I)

_MONTH = r"Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?"
_MONTH_VALUES = {name: i for i, name in enumerate(
    ('jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'), 1)}


def _calendar_months(text: str):
    for match in re.finditer(r"\b(?:" + _MONTH + r")\b", text, re.I):
        # May and March are also verbs. Require calendar syntax for them.
        if match[0].lower() in ('may', 'march') and not (
            re.search(r"\b(?:in|since|during|by|after|before|until|from|of|this|last|next)\s*$", text[:match.start()], re.I)
            or re.match(r"\s+(?:\d|estimates?\b|results?\b|data\b|figures?\b|forecasts?\b|earnings\b|looms?\b)", text[match.end():], re.I)
        ):
            continue
        yield match


def _relative_calendar_periods(text: str) -> set[tuple[str, str]]:
    """Relative calendar periods are material even when no date is numeric."""
    result = set()
    cn_units = {'year': '年', 'quarter': '季(?:度)?', 'month': '个?月', 'week': '周|星期'}
    cn_prefixes = {'previous': '上|上一|前一', 'current': '本|这(?:一|个)?', 'next': '下|下一'}
    en_prefixes = {'previous': 'last|previous', 'current': 'this|current', 'next': 'next|following'}
    for unit, cn in cn_units.items():
        for direction, en in en_prefixes.items():
            if (re.search(r'\b(?:' + en + r')\s+' + unit + r'\b', text, re.I)
                    or re.search(r'(?:' + cn_prefixes[direction] + r')(?:' + cn + r')'
                                 + (r'(?!多|余|(?:以)?来|新[高低])' if direction == 'next' else ''), text)):
                result.add((direction, unit))
    for word, direction in {'去年': 'previous', '今年': 'current', '明年': 'next', '次年': 'next'}.items():
        if word in text:
            result.add((direction, 'year'))
    return result


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
    for match in reversed(list(_calendar_months(text))):
        result[("month", _MONTH_VALUES[match[0][:3].lower()])] += 1
        text = text[:match.start()] + ' ' + text[match.end():]
    def month_number(match):
        result[("month", int(match[1]))] += 1
        return ' '
    calendar_counts = dict(zip(('一', '二', '三', '四', '五', '六', '七', '八', '九', '十', '十一', '十二'), range(1, 13), strict=True))
    text = re.sub(r"(?<![一二三四五六七八九十])(十二|十一|十|[一二三四五六七八九])(?=月)",
                  lambda m: str(calendar_counts[m[1]]), text)
    text = re.sub(r"(?<!\d)(1[0-2]|[1-9])\s*月", month_number, text)
    def quarter(match):
        raw = match[1] or match[2]
        result[("quarter", int(raw) if raw.isdigit() else '一二三四'.index(raw) + 1)] += 1
        return ' '
    text = re.sub(r"\bQ([1-4])\b|(?<![上下前后])第?([一二三四1-4])季度", quarter, text, flags=re.I)
    # Standalone calendar years match English "by 2030"; full dates were handled above.
    text = re.sub(r"(?<!\d)([12]\d{3})\s*年", r"\1", text)
    # Normalize written durations only, never arbitrary words or company names.
    en_counts = dict(zip(["one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve"], range(1, 13), strict=True))
    zh_counts = dict(zip(["一", "二", "三", "四", "五", "六", "七", "八", "九", "十", "十一", "十二"], range(1, 13), strict=True))
    zh_counts["两"] = 2
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
    text = re.sub(r"[一二三四五六七八九]?十[一二三四五六七八九]?(?=多?年|个月|天|周)|[一二两三四五六七八九](?=多?年|个月|天|周)", chinese_count, text)
    text = re.sub(r"(\d+)多年", r" more than \1年", text)
    text = re.sub(r"(\d+)(年|个月|天|周)多", r" more than \1\2", text)
    text = re.sub(r'(?<=\d)\s+consecutive(?=\s+(?:years?|months?|days?|weeks?)\b)', '', text, flags=re.I)
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
        before = text[max(0, match.start() - 20):match.start()]
        before = re.sub(r"(?:US\$|HK\$|\$|€)\s*$", "", before).rstrip()
        # "over seven years" is a service period, unlike "over $7 billion".
        greater = r"(?:\bmore than|超过|逾)\s*$"
        if unit not in ('years', 'months', 'weeks', 'days'):
            greater = r"(?:\bmore than|\bover|超过|逾)\s*$"
        if re.search(greater, before, re.I):
            result[("bound", value, unit, "gt")] += 1
        elif re.search(r"(?:\bup to|(?:最多|最高|至多)(?:可|将)?(?:增派|派遣|提供|借出|发行|投入|投资|支付|筹集|融资)?(?:达|达到)?)\s*$", before, re.I):
            result[("bound", value, unit, "le")] += 1
        elif re.search(r"(?:\bless than|\bunder|不足|少于|不到)\s*$", before, re.I):
            result[("bound", value, unit, "lt")] += 1
    return result


def _contains(text: str, alias: str) -> bool:
    pattern = re.escape(alias)
    if alias.isascii():
        # English acronym plurals are grammar, not a new identifier: GPUs/GPU.
        # Only a lowercase plural suffix is optional; AWS and GPT-6 stay exact.
        if re.fullmatch(r"[A-Z]{2,}", alias):
            pattern += r"(?-i:s)?"
        pattern = rf"(?<![A-Za-z0-9_]){pattern}(?![A-Za-z0-9_])"
    return bool(re.search(pattern, text, re.I))


def _entity_order(text: str) -> list[str]:
    positions = []
    for entity, aliases in _ENTITIES.items():
        hits = []
        for alias in aliases:
            pattern = re.escape(alias)
            if alias.isascii():
                if re.fullmatch(r"[A-Z]{2,}", alias):
                    pattern += r"(?-i:s)?"
                pattern = rf"(?<![A-Za-z0-9_]){pattern}(?![A-Za-z0-9_])"
            hits.extend(m.start() for m in re.finditer(pattern, text, re.I))
        if hits:
            positions.append((min(hits), entity))
    return [entity for _, entity in sorted(positions)]


def _claim_entity_order(text: str) -> list[str]:
    # Attribution order is independent of the actor/object order in the claim.
    name = r"(?:[A-Z][a-z]+(?: [A-Z][a-z]+){0,3}|[一-鿿·]{2,12})"
    patterns = [r"[，,]\s*(?P<speaker>" + name + r")\s*(?:称|表示|说)(?=\s*(?:[-–—|]|[。.]?$))",
                r",\s*(?P<speaker>" + name + r")\s+(?i:says|said)\b(?=\s*(?:[-–—|]|[。.]?$))",
                r"^(?P<speaker>" + name + r")\s+(?i:says|said)\s+",
                r"^(?P<speaker>" + name + r")\s*(?:称|表示|说)[，,:：]?\s*"]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            # The same speaker may use an English or Chinese spelling. Keep
            # actor/object order in the claim; normalize only the attribution.
            from src.collectors.figures import _FINNHUB_FALLBACK_ALIASES

            raw_name = match['speaker'].strip()
            # Full names must match a registered full alias, not just a surname.
            # This prevents Alex Altman from becoming Sam Altman.
            identity = next((person for person, aliases in _FINNHUB_FALLBACK_ALIASES.items()
                             if raw_name.casefold() in {alias.casefold() for alias in (person, *aliases)}), raw_name.casefold())
            speaker = [identity]
            claim = text[:match.start()] + text[match.end():]
            return ['speaker:' + '/'.join(speaker), *_entity_order(claim)]
    return _entity_order(text)


def _scoped_states(text: str) -> set[tuple]:
    states = set()
    # Bind polarity/modality to the event clause so a 'not' elsewhere cannot
    # bless a reversed approval/completion assertion.
    for clause in re.split(r"[，,；;。!?]|\bbut\b|但是|但", text, flags=re.I):
        for event in ("approval", "completion", "acquisition", "launch", "payment", "person_release", "asset_release", "reserve_drawdown", "pause", "cancel", "delay"):
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


def _localization_spans(text: str) -> list[tuple[int, int, str]]:
    aliases = {**_LOCALIZED_TERMS_V9, **{name: name for name in _LOCALIZED_TERMS_V9.values()}}
    # Diplomatic direction compounds name countries too; never treat a bare
    # single character inside a product/company name as a country.
    countries = {'China': '中国', 'United States': '美国', 'United Kingdom': '英国', 'Japan': '日本', 'South Korea': '韩国', 'Germany': '德国', 'France': '法国', 'Russia': '俄罗斯'}
    aliases.update(countries)
    aliases.update({name: name for name in countries.values()})
    for short, full in {'华': '中国', '美': '美国', '英': '英国', '日': '日本', '韩': '韩国', '德': '德国', '法': '法国', '俄': '俄罗斯'}.items():
        aliases.update({prefix + short: full for prefix in ('对', '赴', '访', '输')})
    # Longest-first matching treats Bank of Japan as an institution, not as a
    # separate country mention. Product identifiers are outside this glossary.
    alternatives = []
    for alias in sorted(aliases, key=len, reverse=True):
        alternatives.append((r'(?<![A-Za-z])' if alias.isascii() else '') + re.escape(alias)
                            + (r'(?![A-Za-z])' if alias.isascii() else ''))
    lookup = {key.casefold(): value for key, value in aliases.items()}
    return [(match.start(), match.end(), lookup[match[0].casefold()])
            for match in re.finditer('|'.join(alternatives), text, re.I)]


def _localization_mentions(text: str) -> list[str]:
    return [name for _, _, name in _localization_spans(text)]


def _localized_amount_bindings(text: str) -> dict:
    """Bind explicit monetary amounts to nearby locations within each clause.

    Compare relations rather than word order: translating a background clause
    first must not swap the geography of a monetary investment. Ties are kept
    as ambiguous, never resolved by the order in which a name appears.
    """
    result = {}
    for clause in re.split(r'[，；;。]|(?<!\d),(?!\d)', text):
        places = _localization_spans(clause)
        if not places:
            continue
        for match in _NUMBER.finditer(clause):
            fragment = clause[max(0, match.start() - 3):match.end()]
            money = Counter({key: n for key, n in _quantities(fragment).items()
                             if len(key) == 2 and key[1] in {'USD', 'HKD', 'EUR'}})
            if not money:
                continue
            distances = [(max(start - match.end(), match.start() - end, 0), name)
                         for start, end, name in places]
            nearest = min(distance for distance, _ in distances)
            names = tuple(sorted({name for distance, name in distances if distance == nearest}))
            result.setdefault(names, Counter()).update(money)
    return result


def _capital_amount_bindings(text: str) -> dict:
    """Keep authorization balances separate from increments and execution.

    A bag of correct dollar amounts still permits swapping their meanings.
    Bind each marked amount clause to a role for capital-return reporting.
    """
    if not re.search(r"buyback|repurchas|回购", text, re.I):
        return {}
    result = {}
    for clause in re.split(r"[，；;。]|(?<!\d),(?!\d)|\bwhile\b|\band bringing\b", text, flags=re.I):
        amounts = Counter({key: count for key, count in _quantities(clause).items()
                           if len(key) == 2 and key[1] in {'USD', 'HKD', 'EUR'}})
        if not amounts:
            continue
        # Remaining is more specific than 'total remaining'. Never collapse
        # remaining authorization into lifetime repurchases or total spending.
        if re.search(r"\bremaining\b|剩余|尚余|可用余额", clause, re.I):
            role = 'remaining_authorization'
        elif re.search(r"\badditional\b|新增|额外|追加", clause, re.I):
            role = 'additional_authorization'
        elif re.search(r"\b(?:spent|repurchased|bought back|executed)\b|已(?:经)?(?:回购|执行)|实际(?:执行|回购)", clause, re.I):
            role = 'executed_repurchases'
        elif re.search(r"\btotal\b|总额|合计|累计", clause, re.I):
            role = 'total'
        else:
            continue
        result.setdefault(role, Counter()).update(amounts)
    return result


def _economic_amount_roles(text: str) -> dict:
    """Bind monetary quantities to explicit economic nouns, independent of issuer."""
    roles = {
        'managed_assets': r'assets under management|managed assets|管理资产|资产管理规模',
        'market_size': r'market opportunity|addressable market|市场空间|潜在市场规模',
        'revenue': r'\brevenues?\b|营收|收入',
        'expenditure': r'\b(?:spent|spend|spending|invested|invests?)\b|实际投入|投入|花费|支出',
    }
    result = {}
    for clause in re.split(r'[，；;。]|(?<!\d),(?!\d)', text):
        amounts = Counter({key: n for key, n in _quantities(clause).items()
                           if len(key) == 2 and key[1] in {'USD', 'HKD', 'EUR'}})
        matches = [role for role, pattern in roles.items() if re.search(pattern, clause, re.I)]
        # Ambiguous clauses are not assigned an invented single economic role.
        if amounts and len(matches) == 1:
            result.setdefault(matches[0], Counter()).update(amounts)
    return result


def translation_errors(original: str, translated: str) -> list[str]:
    from src.processors.news_selection import strip_source_prefix

    original = strip_source_prefix(unicodedata.normalize('NFKC', original))
    translated = strip_source_prefix(unicodedata.normalize('NFKC', translated))
    # Release of people is not a product launch. Preserve the rest of the clause,
    # including negation, numbers and entities, for the checks below.
    original = re.sub(r"\breleas(?:e|es|ed|ing)(?=\s+(?:(?:the|a|two|three|\d+)\s+)?(?:suspects?|prisoners?|hostages?)\b)",
                      "person_release", original, flags=re.I)
    # A court ordering somebody to pay is an obligation, not a tentative plan.
    original = re.sub(r"\b(orders?|ordered|requires?|required)(\s+[^.;!?]{1,80}?)\bto\s+(pay)\b",
                      r"\1\2\3", original, flags=re.I)
    # Starting an investigation is a procedural event, not a product launch.
    original = re.sub(r"\blaunch(?:es|ed|ing)?(?=\s+(?:(?:an?|the|broad|new|formal|antitrust|regulatory|criminal|civil|independent|joint|sweeping|comprehensive)\s+){0,3}investigation\b)", "opens", original, flags=re.I)
    translated = re.sub(r"(?:启动|发起)(?=(?:对[^，。；,;]{1,40}的)?(?:广泛|全面|正式|新|反垄断|刑事|民事)?调查)", "展开", translated)
    # Agentic is already an agent concept; a Chinese explanatory AI prefix
    # does not introduce an unrelated acronym. Preserve explicit source AI tokens.
    if re.search(r'\bagentic\b', original, re.I) and not re.search(r'\bAI\b', original):
        translated = re.sub(r'\bAI\s*(?=智能体)', '', translated)
    original = normalize_event_text(original)
    translated = normalize_event_text(translated)
    errors = []
    if re.search(r"\\[nr]", translated) and not re.search(r"\\[nr]", original):
        errors.append("serialized_translation_boundary")
    if _relative_calendar_periods(original) != _relative_calendar_periods(translated):
        errors.append("relative_calendar_period")
    if re.search(r"\bmake peace with\b", original, re.I) and '和解' in translated:
        errors.append('literal_financial_idiom')
    source_roles = _economic_amount_roles(original)
    output_roles = _economic_amount_roles(translated)
    if source_roles and output_roles and source_roles != output_roles:
        errors.append('economic_amount_role')
    places = _localization_mentions(original)
    # Only explicit source names constrain their localized equivalents. Existing
    # finance conventions (Treasuries -> 美国国债) must not invent an extra
    # source entity merely because the explanatory Chinese includes a country.
    if Counter(places) != Counter(name for name in _localization_mentions(translated) if name in places):
        errors.append('localized_entity_binding')
    if len(set(places)) > 1 and _localized_amount_bindings(original) != _localized_amount_bindings(translated):
        errors.append('localized_amount_binding')
    if document_types(original) != document_types(translated):
        errors.append('document_type')
    if (re.search(r"\bsummary\b|摘要|概要", original, re.I)
            and 'meeting_minutes' not in document_types(original)
            and 'meeting_minutes' in document_types(translated)):
        errors.append('summary_not_minutes')
    # Superlatives with plural subjects must not silently become a single
    # country/company/bank. Require explicit plurality in Chinese for this
    # otherwise ambiguous construction; this is not a universal grammar proof.
    for noun, chinese in {'economies': '经济体', 'companies': '公司|企业', 'banks': '银行',
                          'markets': '市场', 'countries': '国家'}.items():
        if (re.search(r"\b(?:biggest|largest|smallest|leading|major|top)\s+" + noun + r"\b", original, re.I)
                and not re.search(r"(?:各|多|几|数|主要|之一|们|两|三|四|五).{0,12}(?:" + chinese + r")|(?:" + chinese + r").{0,4}(?:之一|们)", translated)):
            errors.append('plural_subject_scope:' + noun)
    if (re.search(r"\b(?:government|sovereign) debt\b.*\b(?:rout|selloff|sell-off|selling)\b", original, re.I)
            and re.search(r"政府债务(?:暴跌|下跌|下降|锐减)", translated)):
        errors.append("bond_price_not_debt_stock")
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
    if _capital_amount_bindings(original) != _capital_amount_bindings(translated):
        errors.append('capital_amount_scope')
    if _quantities(original) != _quantities(translated):
        errors.append('quantities_or_units')
    bindings = _financial_bindings(original)
    if len(bindings) > 1 and bindings != _financial_bindings(translated):
        errors.append('financial_metric_binding')
    # Full and standalone calendar dates were checked above; their month names
    # must not become modal verbs (May estimates -> 5 月估算).
    for match in reversed(list(_calendar_months(original))):
        original = original[:match.start()] + 'calendar_month' + original[match.end():]
    # Adjectival "paid plans/workflows" describes pricing, not a completed
    # payment. Keep actual "has paid" and monetary transactions untouched.
    original = re.sub(r"(?<!has )(?<!have )(?<!had )\bpaid\s+(?=workflows?\b|plans?\b|services?\b|subscriptions?\b|tiers?\b|features?\b)",
                      "fee_based ", original, flags=re.I)
    # Noun downturns are states, unlike the directional verb "sales slump".
    original = re.sub(r"\b(?:a|the)\s+(?:[\w-]+\s+){0,2}slump\b", "downturn", original, flags=re.I)
    # A dated participial launch is a scheduled event, not an assertion that it
    # has already launched. Scope this to the explicit calendar construction.
    original = re.sub(r"\bLaunching\s+(?=calendar_month\s+\d)", "will launch ", original, flags=re.I)
    # Reported results beating a benchmark are not an uncertain forecast.
    if re.search(r"\b(?:beat\w*|above|exceed\w*).{0,70}(?:expectations|estimates)\b", original, re.I):
        translated = re.sub(r"((?:超过|超出|高于).{0,20})(?:预期|预估)", r"\1市场基准", translated)
    for condition, pattern in {
        "until": r"\buntil\b|直到|直至|(?:推迟|延后|延期)至",
        "unless": r"\bunless\b|除非",
    }.items():
        if bool(re.search(pattern, original, re.I)) != bool(re.search(pattern, translated, re.I)):
            errors.append("condition:" + condition)
    for name, pattern in {'negation': _NEGATION, 'modality': _MODALITY, **{k: v for k, v in _EVENTS.items() if k != 'announcement'}}.items():
        source_present = bool(re.search(pattern, original, re.I))
        translated_present = bool(re.search(pattern, translated, re.I))
        if name == 'modality' and re.search(r'\blooms?\b', original, re.I):
            translated_present |= bool(re.search(r'临近|迫近|逼近|将至|面临|迎来', translated))
        if source_present != translated_present:
            errors.append(name)
    for entity, aliases in _ENTITIES.items():
        if any(_contains(original, alias) for alias in aliases) != any(_contains(translated, alias) for alias in aliases):
            errors.append('entity:' + entity)
    if _claim_entity_order(original) != _claim_entity_order(translated):
        errors.append('entity_order')
    if _scoped_states(original) != _scoped_states(translated):
        errors.append('event_scope')
    # Preserve identifiers, model names and acronyms literally (including unknown ones).
    # Match versioned model names first and greedily; a grammatical acronym
    # plural must not erase a model suffix (e.g. Claude-3-Sonnet -> Claude-3-Opus).
    # Delimited editorial format labels are ordinary translatable words, not
    # tickers/model names. Remove only this leading syntactic role from the
    # identifier comparison, leaving the immutable source and other checks intact.
    original = re.sub(r'^(?:(?:FULL|EXCLUSIVE|LIVE)\s+(?:INTERVIEW|REPORT|COVERAGE)|BREAKING\s+NEWS|EXCLUSIVE)\s*[:：-]\s*', '', original, flags=re.I)
    translated = re.sub(r'^(?:(?:完整|独家|现场)(?:采访|访谈|报道)|突发新闻|独家)\s*[:：-]\s*', '', translated)
    tokens = re.findall(r'\b(?:[A-Za-z]+[-.]\d[\w.-]*|[A-Z]{2,}[A-Z0-9-]*s?)\b', original + ' ' + translated)
    identifiers = {token[:-1] if re.fullmatch(r"[A-Z]{2,}s", token) else token for token in tokens}
    location_tokens = {'NEW', 'YORK'} if _contains(original, 'New York') else set()
    aliased_identifiers = {alias for aliases in _ENTITIES.values() if len(aliases) > 1
                          for alias in aliases if re.fullmatch(r'[A-Z]{2,}', alias)}
    for identifier in identifiers - {'USD', 'HKD', 'EUR', 'CEO', 'US', 'UK'} - location_tokens - aliased_identifiers:
        if _contains(original, identifier) != _contains(translated, identifier):
            errors.append('identifier:' + identifier)
    if len(translated) > max(100, len(original) * 2):
        errors.append('expanded_text')
    return errors
