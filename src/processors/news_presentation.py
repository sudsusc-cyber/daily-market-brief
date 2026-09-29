"""Deterministic display cleanup after source verification, never source mutation."""

import re

from src.processors.news_selection import plain_source

# Only publisher metadata at the end of a sentence is removed. Attribution in
# the sentence (e.g. 'Reuters reports') remains part of the source's claim.
_PUBLISHERS = {
    'wsj': ('WSJ', 'wsj.com', 'The Wall Street Journal', '华尔街日报'),
    'reuters': ('Reuters', '路透社'),
    'bloomberg': ('Bloomberg', '彭博社'),
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
    '腾讯': r'0?0700\.HK|700\.HK', 'Tencent': r'0?0700\.HK|700\.HK',
}
_FINANCIAL_TERMS = {'Federal Reserve': '美联储', 'Treasuries': '美国国债'}


def publication_text(text: str, *, source_name: str = '') -> str:
    """Remove presentation-only metadata without rewriting financial assertions."""
    text = plain_source(text)
    source = plain_source(source_name)
    aliases = {source} if source else set()
    for names in _PUBLISHERS.values():
        if source.casefold() in {name.casefold() for name in names}:
            aliases.update(names)
    for name in sorted(aliases, key=len, reverse=True):
        text = re.sub(r'(?:\s*[-–—|]+\s*|\s{2,})' + re.escape(name) + r'[。.]?\s*$', '', text, flags=re.I)
    text = _LISTING.sub('', text)
    for name, ticker in _BARE_LISTINGS.items():
        text = re.sub(r'(?<![A-Za-z])(' + re.escape(name) + r')\s*[（(](?:' + ticker + r')[)）]',
                      r'\1', text, flags=re.I)
    for name, short_name in _LEGAL_NAMES.items():
        text = re.sub(r'(?<![A-Za-z])' + re.escape(name) + r'(?![A-Za-z])', short_name, text, flags=re.I)
    for name, translation in _FINANCIAL_TERMS.items():
        text = re.sub(r'\b' + re.escape(name) + r'\b', translation, text)
    # These commas separate prose, not digits in a financial quantity.
    text = re.sub(r'(?<!\d),|,(?!\d)', '，', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'(?<=[一-鿿]) +(?=[一-鿿])', '', text)
    return text.strip()
