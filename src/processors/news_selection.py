"""Reject reproducible editorial noise before asking a model to rank news."""

import html
import re

from src.processors.html_safe import strip_all_tags


def plain_source(text: str) -> str:
    # Decode before stripping, then escape only once at final HTML construction.
    for _ in range(2):
        text = html.unescape(text or '')
    return strip_all_tags(text).strip()


def sentences(text: str) -> list[str]:
    text = plain_source(text)
    # Split only where the next sentence begins; keep decimal points, initials,
    # month abbreviations and company suffixes within their complete sentence.
    return [s.strip() for s in re.split(r'(?<=[。！？])|(?<=[.!?])\s+(?=[A-Z])', text) if s.strip()]


_PRICE_EDITORIAL = re.compile(
    r'which.*(?:stock|buy)|better stock|stock.*(?:to buy|worth buying)|undervalued.*(?:view|compelling)'
    r'|(?:stock|shares?|\([A-Z]+\)).*(?:is up|is down|holds flat|rallies|surges|jumps|slumps)'
    r'|哪.*股票|值得买|股价.*(?:上涨|下跌|飙升)', re.I)
_BUSINESS_FACT = re.compile(
    r'\b(?:reported?.*(?:results|earnings|revenue)|earnings|revenue|sales|renew\w*.*(?:licen\w*|agreement)'
    r'|(?:plans?|will|agrees? to) invest|announced|appoint\w*|acqui\w*|merger|launch\w*'
    r'|settlement|data cent(?:er|re)|cloud.*(?:infrastructure|capacity)|dividend|buyback)\b'
    r'|业绩|营收|利润|投资|发布|任命|续签|收购|并购|结算|分红|回购', re.I)
_RATING_SERVICE = re.compile(
    r"(?:Moody[’']?s|穆迪).*(?:affirms?|upgrades?|downgrades?|cuts?|lifts?|上调|下调|确认|维持).*?(?:ratings?|评级|outlook|展望)", re.I)


def factual_excerpt(item) -> str:
    """Prefer a complete operational sentence to an opinion/question headline.

    The immutable title and summary remain attached for context and auditing.
    RSS snippets that merely repeat the headline are not extra evidence.
    """
    title = plain_source(getattr(item, 'title', ''))
    summary = getattr(item, 'summary', '') or getattr(item, 'snippet', '')
    eligible = []
    for sentence in sentences(summary):
        if (len(sentence) >= 30 and _BUSINESS_FACT.search(sentence) and not _PRICE_EDITORIAL.search(sentence)
                and sentence not in title and title not in sentence):
            eligible.append(sentence)
    # Prefer the reported financial result over a sentence merely announcing
    # the earnings date; source-company binding is supplied by the collector.
    for sentence in eligible:
        if re.search(r"earnings|revenue|sales|每股|营收|利润", sentence, re.I) and re.search(r"[$%]|美元|%", sentence):
            return sentence
    return eligible[0] if eligible else title


def company_candidate(item, ticker: str) -> bool:
    title = plain_source(getattr(item, 'title', ''))
    if (ticker == 'MCO' and _RATING_SERVICE.search(title)
            and not re.search(r'earnings|revenue|profit|营收|盈利|利润|业绩', title, re.I)):
        return False
    return not _PRICE_EDITORIAL.search(title) or factual_excerpt(item) != title


def frontier_candidate(item) -> bool:
    title = plain_source(getattr(item, 'title', ''))
    return not _PRICE_EDITORIAL.search(title) or factual_excerpt(item) != title


def meaningful_quote(item) -> bool:
    text = plain_source(f"{item.title} {item.snippet or ''}")
    generic = re.search(r'great to see|very excited|very exciting|AI is the future|很棒|很兴奋|AI 是未来', text, re.I)
    return not generic or bool(re.search(r'\d|capex|capital spending|billion|million|资本开支|产能|投资额', text, re.I))


def neutral_macro_topic(items) -> str:
    text = " ".join(plain_source(item.title) for item in items)
    for topic, pattern in (
        ("国际贸易", r"tariff|trade|关税|贸易"),
        ("地缘政治", r"Russia|Ukraine|Iran|ceasefire|战争|停火|俄乌"),
        ("资本流动", r"capital flows|fund flows|foreign capital|资金流|外资"),
        ("货币政策", r"\bFed\b|FOMC|美联储|降息|加息"),
        ("通胀数据", r"inflation|\bCPI\b|通胀"),
    ):
        if re.search(pattern, text, re.I):
            return topic
    return "宏观动态"
