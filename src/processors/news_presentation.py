"""Deterministic display cleanup after source verification, never source mutation."""

import re

from src.processors.news_selection import plain_source

# Only publisher metadata at the end of a sentence is removed. Attribution in
# the sentence (e.g. 'Reuters reports') remains part of the source's claim.
_PUBLISHERS = {
    'wsj': ('WSJ', 'wsj.com', 'The Wall Street Journal', '华尔街日报'),
    'reuters': ('Reuters', '路透社'),
    'bloomberg': ('Bloomberg', '彭博社'),
    'abc australia': ('ABC News Australia', 'ABC News', '澳大利亚广播公司'),
    'financial times': ('Financial Times', 'FT', '金融时报'),
}
_LISTING = re.compile(
    r'\s*[（(]\s*(?:NASDAQ(?:GS|GM|CM)?|NYSE(?:ARCA|AMERICAN)?|AMEX|HKEX|SEHK|LSE)'
    r'\s*[:：]\s*[A-Z0-9][A-Z0-9.^-]{0,14}\s*[)）]', re.I)
# Legal-name boilerplate -> ordinary editorial names, with an explicit mapping.
_LEGAL_NAMES = {
    'QUALCOMM Incorporated': '高通',
    'Qualcomm Inc.': '高通',
    'Apple Inc.': '苹果',
    'Microsoft Corporation': '微软',
    'Alphabet Inc.': 'Alphabet',
    'The Coca-Cola Company': '可口可乐',
    'Mastercard Incorporated': '万事达',
}
_BARE_LISTINGS = {
    'Microsoft': 'MSFT', 'Apple': 'AAPL', 'Qualcomm': 'QCOM',
    'Mastercard': 'MA', 'Alphabet': 'GOOG|GOOGL', 'NVIDIA': 'NVDA',
    'Berkshire Hathaway': r'BRK[.-][AB]', 'Costco': 'COST',
    '高通': 'QCOM', '苹果': 'AAPL', '微软': 'MSFT', '万事达': 'MA',
    '泡泡玛特': r'0?9992\.HK', 'POP MART': r'0?9992\.HK',
    '腾讯控股': r'0?0700\.HK|700\.HK', '腾讯': r'0?0700\.HK|700\.HK', 'Tencent': r'0?0700\.HK|700\.HK',
}
_FINANCIAL_TERMS = {'Federal Reserve': '美联储', 'Fed': '美联储', 'Treasuries': '美国国债',
                    'Strait of Hormuz': '霍尔木兹海峡', 'Hormuz': '霍尔木兹海峡',
                    'European Union': '欧盟', 'EU': '欧盟', 'Germany': '德国',
                    'Saudi Arabia': '沙特阿拉伯', 'Australia': '澳大利亚', 'Iran': '伊朗', 'Satya Nadella': '萨提亚·纳德拉'}


def publication_text(text: str, *, source_name: str = '') -> str:
    """Remove presentation-only metadata without rewriting financial assertions."""
    text = plain_source(text)
    source = plain_source(source_name)
    aliases = {source} if source else set()
    for names in _PUBLISHERS.values():
        if source.casefold() in {name.casefold() for name in names}:
            aliases.update(names)
    # Nested syndication tails may name another known publisher. Only a
    # delimited trailing label is metadata; in-sentence attribution is retained.
    aliases.update(name for names in _PUBLISHERS.values() for name in names)
    for _ in range(4):
        before = text
        for name in sorted(aliases, key=len, reverse=True):
            text = re.sub(r'(?:\s*[-–—|]+\s*|\s{2,})' + re.escape(name) + r'[。.]?\s*$', '', text, flags=re.I)
        if text == before:
            break
    # A standalone read-on teaser adds no fact and promises content absent from the brief.
    # Remove only an exact terminal sentence, never a clause followed by actual details.
    text = re.sub(r'(?:[。.!?]\s*|^)(?:以下是预期情况|以下是你需要了解的内容)[。.!?]?$', '', text).strip()
    text = _LISTING.sub('', text)
    for name, ticker in _BARE_LISTINGS.items():
        text = re.sub(r'(?<![A-Za-z])(' + re.escape(name) + r')\s*[（(](?:' + ticker + r')[)）]',
                      r'\1', text, flags=re.I)
    for name, short_name in _LEGAL_NAMES.items():
        text = re.sub(r'(?<![A-Za-z])' + re.escape(name) + r'(?![A-Za-z])', short_name, text, flags=re.I)
    for name, translation in _FINANCIAL_TERMS.items():
        # A country inside a publisher's proper name is not body geography.
        protected = '|'.join(re.escape(label) for label in sorted(aliases, key=len, reverse=True))
        pattern = protected + r'|(?P<term>\b' + re.escape(name) + r'\b)'
        text = re.sub(pattern, lambda m, translation=translation: translation if m.group('term') else m[0], text)
    # These commas separate prose, not digits in a financial quantity.
    text = re.sub(r'(?<!\d),|,(?!\d)', '，', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'(?<=[一-鿿]) +(?=[一-鿿])', '', text)
    return text.strip()


def voice_text(text: str, person: str) -> str:
    """Move only a neutral leading speaker attribution into its visible byline.

    No surname guessing, global name replacement, or removal of denial/warning
    verbs. Original source and validated translation remain unchanged.
    """
    from src.collectors.figures import FIGURES

    extra_names = {
        '纳德拉': ('萨提亚·纳德拉',), '苏妈': ('苏姿丰',),
        '皮叉': ('桑达尔·皮查伊',), '奥特曼': ('山姆·奥特曼',),
        '巴菲特': ('沃伦·巴菲特',),
    }
    names = next(({cn, en, *extra_names.get(cn, ())} for cn, _, _, en in FIGURES
                  if person in {cn, en}), set())
    if not names:
        return text
    prefix = '|'.join(re.escape(name) for name in sorted(names, key=len, reverse=True))
    role = r'(?:(?:Nvidia|英伟达)\s*(?:CEO|首席执行官)\s*)?' if 'Jensen Huang' in names else ''
    local_name = r'(?:\s*[（(]젠슨 황[)）])?' if 'Jensen Huang' in names else ''
    leading = r'^' + role + r'(?:' + prefix + r')' + local_name + r'\s*'
    match = re.match(leading + r'(?:表示|认为|指出|称|说)\s*[：:，,]?\s*(.+)$', text, re.I)
    if not match:
        # Keep the whole attribution verb in "将 ... 称为 ..."; only the
        # speaker metadata moves to the byline.
        match = re.match(leading + r'((?:将|把).*(?:称为|视为|形容为).+)$', text, re.I)
    if not match:
        return text
    body = match[1].strip()
    # Do not strand an aspect particle or strip a name used as an object.
    if len(body) < 6 or re.match(r'[了过着的：:，,。]|赞|之为|为|作|呼|号|服|出|到', body):
        return text
    return body
