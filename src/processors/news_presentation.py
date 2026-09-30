"""Shared, versioned presentation of verified text across all news sections.

Recognize metadata by its syntax/context. Never infer facts or mutate sources.
Proper-name translations are vocabulary, not issuer-specific cleanup rules.
"""

import re
from dataclasses import asdict, dataclass

from src.config import HOLDINGS
from src.processors.news_selection import plain_source
from src.processors.presentation_vocabulary import (
    _FINANCIAL_TERMS,
    _LEGAL_NAMES,
    _PUBLISHERS,
    COMPANY_DISPLAY_NAMES,
)

PRESENTATION_VERSION = 4
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


def present(text: str, *, source_name: str = "", _version: int = PRESENTATION_VERSION) -> Presentation:
    text = plain_source(text)
    operations = []

    def record(name, updated):
        nonlocal text
        if text != updated:
            operations.append(name)
            text = updated

    source = plain_source(source_name)
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
    pieces = re.split(r"(?<=[。!?！？])", text)
    tail = pieces[-1].strip() or (pieces[-2].strip() if len(pieces) > 1 else "")
    if tail and _TEASER.fullmatch(tail):
        record("read_on_teaser", text[: text.rfind(tail)].rstrip("。.!? "))
    record("qualified_listing", _QUALIFIED.sub("", text))
    record("issuer_listing", _PARENS.sub(lambda m: _known_bare_listing(m, text), text))
    if _version >= 4:
        record("issuer_listing", re.sub(r"\s*[（(](\d{4,5})[)）]",
               lambda m: _known_bare_listing(m, text), text))
    for name, short in _LEGAL_NAMES.items():
        record(
            "entity_display_name",
            re.sub(r"(?<![A-Za-z])" + re.escape(name) + r"(?![A-Za-z])", short, text, flags=re.I),
        )
    for name, translated in _FINANCIAL_TERMS.items():
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
        record_pattern = (r"(?P<country1>[一-鿿]{2,8})?(?P<tenor>\d+(?:\.\d+)?)年期"
                          r"(?P<country2>[一-鿿]{2,8})?国债收益率"
                          r"(?P<move>攀升至|升至|跌至|降至)(?P<year>\d{4})年以来"
                          r"(?P<extreme>最高|最低)(?:水平)?")
        match = re.fullmatch(record_pattern, compact)
        if match and bool(match['country1']) != bool(match['country2']):
            rising = match['move'] in ('攀升至', '升至')
            if rising == (match['extreme'] == '最高'):
                country = match['country1'] or match['country2']
                record("bond_record_word_order", country + match['tenor'] + '年期国债收益率'
                       + ('升至' if rising else '降至') + match['year'] + '年以来' + match['extreme'] + '。')
    return Presentation(text.strip(), tuple(dict.fromkeys(operations)), _version)


def publication_text(text: str, *, source_name: str = "") -> str:
    return present(text, source_name=source_name).text


def voice_text(text: str, person: str) -> str:
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
        return voice_text(output, str(row.get("presentation_speaker", ""))) if row.get("presentation_speaker") else output
    if version == PRESENTATION_VERSION:
        output = publication_text(validated, source_name=str(row.get("source_name", "")))
        return (
            voice_text(output, str(row.get("presentation_speaker", "")))
            if row.get("presentation_speaker")
            else output
        )
    return validated
