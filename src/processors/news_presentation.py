"""Shared, versioned presentation of verified text across all news sections.

Recognize metadata by its syntax/context. Never infer facts or mutate sources.
Proper-name translations are vocabulary, not issuer-specific cleanup rules.
"""

import re
from dataclasses import asdict, dataclass
from decimal import Decimal

from src.config import HOLDINGS
from src.processors.news_selection import plain_source
from src.processors.presentation_vocabulary import (
    _FINANCIAL_TERMS,
    _LEGAL_NAMES,
    _LOCALIZED_TERMS_V9,
    _PUBLISHERS,
    COMPANY_DISPLAY_NAMES,
)

PRESENTATION_VERSION = 14
# Exchange identifiers and listing suffixes are a grammar, independent of issuers.
_EXCHANGES = r"NASDAQ(?:GS|GM|CM)?|NYSE(?:ARCA|AMERICAN)?|AMEX|HKEX|SEHK|LSE|XNAS|XNYS|XHKG|SSE|SZSE|TSX|ASX|TSE|XETRA|EURONEXT"
_QUALIFIED = re.compile(
    r"\s*[（(]\s*(?:(?:"
    + _EXCHANGES
    + r"|股票代码|证券代码|ticker)\s*[:：]\s*[A-Z0-9][A-Z0-9.^-]{0,14}|[A-Z0-9][A-Z0-9-]{0,12}\.(?:HK|L|T|DE|PA|SZ|SS|AX|TO))\s*[)）]",
    re.I,
)
_PARENS = re.compile(r"\s*[（(]([A-Z][A-Z0-9.-]{0,12})[)）]")
_TEASER = re.compile(
    r"(?:以下|下面)(?:是|为)?(?:你|您|投资者)?(?:需要|应该|值得)?(?:了解|关注|期待|预期)(?:的)?(?:情况|内容|要点|事项)?[。.!?]?$",
    re.I,
)


@dataclass(frozen=True)
class Presentation:
    text: str
    operations: tuple[str, ...]
    version: int = PRESENTATION_VERSION

    def audit(self) -> dict:
        return asdict(self)


def _issuer_key(value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip()
    # Strip suffixes before collapsing whitespace; otherwise " Inc." can become
    # an inseparable part of the issuer key. Repetition covers stacked suffixes.
    while True:
        base = re.sub(
            r"(?:控股|股份|有限公司|公司| Corporation| Incorporated| Inc\.?| Wholesale| [ABC])$",
            "",
            value,
            flags=re.I,
        ).strip()
        if base == value:
            return value.casefold()
        value = base


def _known_bare_listing(match: re.Match, text: str) -> str:
    # Bare acronyms like (AI) or (EPS) are not listings. Require issuer metadata
    # already present in the configured holding, next to that issuer's name.
    symbol = match[1].replace("-", ".")
    for holding in HOLDINGS:
        same_hk = (symbol.isdigit() and holding.ticker.endswith('.HK')
                   and int(symbol) == int(holding.ticker[:-3]))
        if symbol == holding.ticker or same_hk:
            prefix = text[: match.start()].rstrip()
            normalized = _issuer_key(prefix)
            for alias in (holding.name, COMPANY_DISPLAY_NAMES.get(holding.ticker, "")):
                name = _issuer_key(alias)
                before = normalized[: -len(name)] if name else normalized
                if (
                    name
                    and normalized.endswith(name)
                    and (not before or not re.search(r"[a-z0-9]$", before))
                ):
                    return ""
    return match[0]


def present(text: str, *, source_name: str = "", original_text: str = "", _version: int = PRESENTATION_VERSION) -> Presentation:
    text = plain_source(text)
    operations = []

    def record(name, updated):
        nonlocal text
        if text != updated:
            operations.append(name)
            text = updated

    if _version >= 13 and re.search(r'[一-鿿]', text):
        for term, chinese in {'tensor processing unit': '张量处理器', 'bug': '故障'}.items():
            record('technical_term', re.sub(r'\b' + re.escape(term) + r'\b', chinese, text, flags=re.I))
        record('redundant_pressure', re.sub(r'在([^。！？]{1,25})压力下承压', r'受到\1的压力', text))
    if _version >= 6:
        from src.processors.news_selection import strip_source_prefix
        record("publisher_prefix", strip_source_prefix(text))
    if _version >= 8:
        def amount(match):
            multiplier = {'B': Decimal(10), 'M': Decimal(100), 'T': Decimal(10000)}[match[3].upper()]
            value = format(Decimal(match[2].replace(',', '')) * multiplier, 'f')
            if '.' in value:
                value = value.rstrip('0').rstrip('.')
            unit = '万' if match[3].upper() == 'M' else '亿'
            currency = {'$': '美元', 'US$': '美元', 'HK$': '港元', '€': '欧元', '£': '英镑'}[match[1].upper()]
            return value + unit + currency
        record("currency_magnitude", re.sub(r"(US\$|HK\$|\$|€|£)\s*(\d+(?:,\d{3})*(?:\.\d+)?)\s*([BMT])(?![A-Za-z0-9])", amount, text, flags=re.I))
        # Dated navigation labels have no event content. Keep the fact after
        # the colon and its date/currency evidence unchanged.
        record("dated_navigation", re.sub(r"^(?:今日股市|股市今日|Stock Market Today)[，,：:]?\s*(?:(?:\d{4}年)?\d{1,2}\s*月\s*\d{1,2}\s*日|[A-Za-z]+\s+\d{1,2})(?:[，,]\s*\d{4})?\s*[:：]\s*", '', text, flags=re.I))
    # Protect only original Chinese clause separators; spaces introduced by
    # English-name localization may still collapse naturally.
    separator = '\ue000'
    if _version >= 6:
        while separator in text:
            separator += '\ue000'
        if _version == 6:
            text = re.sub(r"(?<=[一-鿿]) +(?=[一-鿿])", separator, text)
    source = plain_source(source_name)
    if _version >= 7 and source:
        # A translation can collapse the RSS double-space delimiter. Only remove
        # that tail when the immutable excerpt proves it was publisher metadata.
        raw = plain_source(original_text)
        metadata_tail = re.search(r"(?:\s+[-–—|]\s*|\s{2,})" + re.escape(source) + r"\s*$", raw, re.I)
        if metadata_tail and len(re.findall(re.escape(source), raw, re.I)) == len(re.findall(re.escape(source), text, re.I)):
            record("publisher_tail_from_source", re.sub(r"(?:\s*[-–—|]+\s*|\s+)" + re.escape(source) + r"[。.]?\s*$", "", text, flags=re.I))
    aliases = {source} if source else set()
    aliases.update(n for group in _PUBLISHERS.values() for n in group)
    # Strip only a terminal publisher field, never an attribution in prose.
    for _ in range(4):
        before = text
        for name in sorted(aliases, key=len, reverse=True):
            record(
                "publisher_tail",
                re.sub(
                    r"(?:\s*[-–—|]+\s*|\s{2,})" + re.escape(name) + r"[。.]?\s*$",
                    "",
                    text,
                    flags=re.I,
                ),
            )
        if text == before:
            break
    if _version >= 4 and source:
        # A known publisher following a completed sentence/percentage is footer
        # metadata. Ordinary attribution ("与 Publisher 合作") remains prose.
        record("publisher_tail", re.sub(
            r"(?<=[。.!?！？%％])\s+" + re.escape(source) + r"[。.]?\s*$", "", text, flags=re.I))
    if _version >= 7:
        text = re.sub(r"(?<=[一-鿿]) +(?=[一-鿿])", separator, text)
    pieces = re.split(r"(?<=[。!?！？])", text)
    tail = pieces[-1].strip() or (pieces[-2].strip() if len(pieces) > 1 else "")
    if tail and _TEASER.fullmatch(tail):
        record("read_on_teaser", text[: text.rfind(tail)].rstrip("。.!? "))
    if _version >= 10:
        # Configured issuer names tolerate typographic hyphen/space variants.
        for holding in HOLDINGS:
            if '-' in holding.name:
                pattern = re.escape(holding.name).replace(r'\-', '[- ]')
                record("issuer_spelling", re.sub(r'(?<![A-Za-z])' + pattern + r'(?![A-Za-z])', holding.name, text, flags=re.I))
        def appositive(match):
            phrase = match[1]
            # Only descriptive corporate appositives, never amounts, dates,
            # negation or event-state clauses. Original text remains in audit.
            if re.search(r'\d|未|不|无|否认|宣布|批准|收购|计划', phrase):
                return match[0]
            return ' ' if phrase.startswith('一家') else '，'
        record("corporate_description", re.sub(r'，((?:一家|这是一家)[^，。；]{3,90}(?:公司|企业|机构|云))，', appositive, text))
    qualified = _QUALIFIED
    if _version >= 11:
        qualified = re.compile(_QUALIFIED.pattern.replace(_EXCHANGES, _EXCHANGES + "|TW|OTC|OTCQX|OTCQB"), re.I)
    record("qualified_listing", qualified.sub("", text))
    record("issuer_listing", _PARENS.sub(lambda m: _known_bare_listing(m, text), text))
    if _version >= 4:
        record("issuer_listing", re.sub(r"\s*[（(](\d{4,5})[)）]",
               lambda m: _known_bare_listing(m, text), text))
    for name, short in _LEGAL_NAMES.items():
        name_pattern = re.escape(name)
        if _version >= 11:
            name_pattern = name_pattern.replace(r'\.', r'\.?')
        record(
            "entity_display_name",
            re.sub(r"(?<![A-Za-z])" + name_pattern + r"(?![A-Za-z])", short, text, flags=re.I),
        )
    terms = {**_FINANCIAL_TERMS, **(_LOCALIZED_TERMS_V9 if _version >= 9 else {})}
    from src.processors.news_selection import chinese_prose
    if _version >= 12 and chinese_prose(text):
        from src.collectors.figures import FIGURES
        display = {'苏妈': '苏姿丰', '皮叉': '桑达尔·皮查伊', '奥特曼': '山姆·奥特曼', '纳德拉': '萨提亚·纳德拉'}
        terms.update({en: display.get(cn, cn) for cn, _, _, en in FIGURES if re.search('[一-鿿]', cn)})
    for name, translated in terms.items():
        protected = "|".join(re.escape(n) for n in sorted(aliases, key=len, reverse=True))
        pattern = protected + r"|(?P<term>\b" + re.escape(name) + r"\b)"
        record(
            "entity_display_name",
            re.sub(
                pattern,
                lambda m, translated=translated: translated if m.group("term") else m[0],
                text,
            ),
        )
    record(
        "prose_spacing",
        re.sub(
            r"(?<=[一-鿿]) +(?=[一-鿿])",
            "",
            re.sub(r"[ \t]+", " ", re.sub(r"(?<!\d),|,(?!\d)", "，", text)),
        ),
    )
    if _version >= 4:
        # A fully parsed bond-yield record has an identical instrument, tenor,
        # direction, comparison period and extreme. Do not normalize partial
        # matches, qualifiers, forecasts, levels or additional clauses.
        compact = re.sub(r"\s+", "", text).rstrip('。.')
        move = r"攀升至|升至|跌至|降至"
        if _version >= 5:
            # Record highs/lows can also be expressed with neutral reach verbs.
            # Normalize only a complete, unqualified record; the extreme fixes
            # direction. Version 4 replay retains its exact historical rules.
            move += r"|触及|达到"
        record_pattern = (r"(?P<country1>[一-鿿]{2,8})?(?P<tenor>\d+(?:\.\d+)?)年期"
                          r"(?P<country2>[一-鿿]{2,8})?国债收益率"
                          r"(?P<move>" + move + r")(?P<year>\d{4})年以来"
                          r"(?P<extreme>最高|最低)(?:水平)?")
        match = re.fullmatch(record_pattern, compact)
        if match and bool(match['country1']) != bool(match['country2']):
            rising = (match['extreme'] == '最高' if match['move'] in ('触及', '达到')
                      else match['move'] in ('攀升至', '升至'))
            if rising == (match['extreme'] == '最高'):
                country = match['country1'] or match['country2']
                record("bond_record_word_order", country + match['tenor'] + '年期国债收益率'
                       + ('升至' if rising else '降至') + match['year'] + '年以来' + match['extreme'] + '。')
    if _version >= 6:
        text = text.replace(separator, ' ')
    return Presentation(text.strip(), tuple(dict.fromkeys(operations)), _version)


def publication_text(text: str, *, source_name: str = "") -> str:
    return present(text, source_name=source_name).text


def voice_text(text: str, person: str, *, _version: int = PRESENTATION_VERSION) -> str:
    from src.collectors.figures import FIGURES

    # Alias data identifies the speaker; neutral-attribution grammar is shared.
    aliases = {
        "纳德拉": ("萨提亚·纳德拉",),
        "苏妈": ("苏姿丰",),
        "皮叉": ("桑达尔·皮查伊",),
        "奥特曼": ("山姆·奥特曼",),
        "巴菲特": ("沃伦·巴菲特",),
    }
    names = next(
        ({cn, en, *aliases.get(cn, ())} for cn, _, _, en in FIGURES if person in {cn, en}), {person}
    )
    if not person:
        return text
    name = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    role = r"(?P<role>(?:[^，。；:：!?]{1,50}?\s*)?(?:CEO|CFO|CTO|首席执行官|董事长|总裁)\s*)?"
    # Parenthesized local-script spelling may accompany an exact known name.
    spelling = r"(?:\s*[（(][가-힣ぁ-ゟ゠-ヿ· ]{2,20}[)）])?"
    leading = "^" + role + "(?:" + name + ")" + spelling + r"\s*"
    if _version >= 14:
        pieces = re.split(r'\s*——\s*|\s+[—–]\s+', text, maxsplit=1)
        if len(pieces) == 2:
            background, speech = pieces
            price = re.match(r'^(?P<issuer>[A-Za-z][A-Za-z .&-]{0,35}|[一-鿿]{2,12})\s*股价', background)
            if price and re.match(leading + r'(?:表示|认为|指出|称|说)', speech):
                body = voice_text(speech, person, _version=_version)
                if body != speech:
                    if body.startswith('公司'):
                        body = price['issuer'].strip() + body[2:]
                    # Preserve numerical/deal context, but lead with the speech.
                    has_context = re.search(r'投资|收购|协议|合同|之后|此前|后', background)
                    return body.rstrip('。') + ('（背景：' + background.rstrip('。') + '）' if has_context else '')
    if _version >= 6:
        # Topic prefixes stay visible; only an exact configured speaker and a
        # neutral reporting verb are elided. Addressed audiences stay in prose.
        topic, separator, rest = text.partition("：")
        if separator and not re.search(r"称|表示|说|否认|警告|said|says|warn", topic, re.I) and re.match(leading, rest.strip(), re.I):
            return topic + separator + voice_text(rest.strip(), person, _version=_version)
        addressed = re.match(leading + r"(?P<audience>对[^，。；：:]{1,40})(?:表示|说|称)\s*[：:]?\s*(?P<body>.+)$", text, re.I)
        if addressed and not re.search(r"称|表示|否认|批评|警告|said|warn|denied", addressed.group("role") or "", re.I):
            return addressed['audience'] + '表示：' + addressed['body']
    match = re.match(leading + r"(?:表示|认为|指出|称|说)\s*[：:，,]?\s*(.+)$", text, re.I)
    if not match:
        match = re.match(leading + r"((?:将|把).*(?:称为|视为|形容为).+)$", text, re.I)
    if not match:
        return text
    if re.search(r"称|表示|否认|批评|警告|said|warn|denied", match.group("role") or "", re.I):
        return text
    body = match[2].strip()
    if len(body) < 6 or re.match(r"[了过着的：:，,。]|赞|之为|为|作|呼|号|服|出|到", body):
        return text
    return body


def company_body(text: str, ticker: str, *, _version: int = PRESENTATION_VERSION) -> str:
    """Remove only a leading issuer repeated by the verified company label."""
    holding = next((h for h in HOLDINGS if h.ticker == ticker), None)
    if not holding:
        return text
    aliases = {holding.name, _issuer_key(holding.name), COMPANY_DISPLAY_NAMES.get(ticker, '')}
    if _version >= 11:
        aliases.add(ticker)
    # Issuer-name vocabulary only; never product names, executives or relevance keywords.
    aliases.update({'GOOG': ('Google', 'Alphabet'), 'TSM': ('TSMC', '台积电', '台積電'),
                    'AXP': ('Amex',)}.get(ticker, ()))
    for name, short in _LEGAL_NAMES.items():
        if short in aliases:
            aliases.add(name)
    for alias in sorted(filter(None, aliases), key=len, reverse=True):
        pattern = re.escape(alias).replace(r'\-', '[- ]')
        match = re.match(pattern + (r'(?![A-Za-z])' if alias.isascii() else ''), text, re.I)
        if match:
            body = text[match.end():].lstrip(' ：:，,│|')
            if _version >= 11:
                # Legal suffixes are part of the issuer, not a new subject.
                body = re.sub(r'^(?:Inc|Corp|Corporation|Incorporated|Ltd|Limited|LLC)\.?(?![A-Za-z])\s*', '', body, flags=re.I)
                # Keep ownership/modifier and coordinated subjects intact.
                if re.match(r"的|旗下|支持的|投资的|和|与|及|['’]s\b", body):
                    return text
            # Possessive subjects must remain explicit (e.g. Google's supplier).
            if len(body) >= 6 and not re.match(r"的|['’]s\b", body):
                return body
    return text


def macro_context_text(text: str, antecedent: str) -> str:
    """Elide only an exact repeated causal subject, retaining all new context."""
    prefix = antecedent + '，因'
    if 8 <= len(antecedent) <= 100 and text.startswith(prefix):
        return '相关背景是' + text[len(prefix):]
    reverse = re.fullmatch(r'(?:由于|因为|因)(.+)，' + re.escape(antecedent) + r'[。.]?', text)
    if 8 <= len(antecedent) <= 100 and reverse:
        return '相关背景是' + reverse[1]
    return text


def replay_presentation(validated: str, row: dict) -> str:
    """Replay the exact transformation contract used by the publication."""
    version = row.get("presentation_version")
    if version in (1, 2):
        from src.processors.legacy_news_presentation import publication_text as old_text
        from src.processors.legacy_news_presentation import voice_text as old_voice

        output = old_text(validated, source_name=str(row.get("source_name", "")))
        return (
            old_voice(output, str(row.get("presentation_speaker", ""))) if version == 2 else output
        )
    if version == 3:
        output = present(validated, source_name=str(row.get("source_name", "")), _version=3).text
        return voice_text(output, str(row.get("presentation_speaker", "")), _version=version) if row.get("presentation_speaker") else output
    if version in (4, 5, 6, 7, 8, 9, 10, 11, 12, 13, PRESENTATION_VERSION):
        output = present(validated, source_name=str(row.get("source_name", "")), original_text=str(row.get("excerpt", "")), _version=version).text
        if version >= 13 and row.get('macro_context_antecedent'):
            output = macro_context_text(output, row['macro_context_antecedent'])
        if version >= 10 and row.get("presentation_company"):
            output = company_body(output, row["presentation_company"], _version=version)
        return (
            voice_text(output, str(row.get("presentation_speaker", "")), _version=version)
            if row.get("presentation_speaker")
            else output
        )
    return validated
