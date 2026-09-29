"""Reject reproducible editorial noise before asking a model to rank news."""

import html
import re
from datetime import date, datetime

from src.processors.html_safe import strip_all_tags

# RSS sometimes joins a promotional standfirst directly to a wire dateline.
# The reporting sentence after the dateline remains an exact source substring.
_WIRE_DATELINE = re.compile(
    r'(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?'
    r'|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)'
    r'\.?\s+\d{1,2},?\s+\d{4}\s*(?:\((?:GLOBE NEWSWIRE|BUSINESS WIRE|PR NEWSWIRE)\))?'
    r'\s*(?:--|—{1,2})\s*', re.I)


def reporting_text(text: str) -> str:
    """Drop a wire dateline/standfirst, never rewrite the reporting sentence."""
    text = plain_source(text)
    dateline = _WIRE_DATELINE.search(text[:600])
    if dateline and re.search(
            r"(?:^|(?<=[a-z]))[A-Z][A-Z .'-]{1,45},\s*(?:[A-Z][A-Za-z .'-]{1,30},\s*)?$",
            text[:dateline.start()]):
        return text[dateline.end():].strip()
    return text


def plain_source(text: str) -> str:
    # Decode before stripping, then escape only once at final HTML construction.
    for _ in range(2):
        text = html.unescape(text or '')
    return strip_all_tags(text).strip()


def sentences(text: str) -> list[str]:
    text = reporting_text(text)
    # Split only where the next sentence begins; keep decimal points, initials,
    # month abbreviations and company suffixes within their complete sentence.
    return [s.strip() for s in re.split(r'(?<=[。！？])|(?<=[.!?])\s+(?=[A-Z])', text) if s.strip()]


def complete_excerpt(text: str, source_name: str = '') -> bool:
    """Reject observable RSS truncation; source-field boundaries are not sentences."""
    text = plain_source(text).strip()
    # Flattened publisher footnotes are not financial quantities. Do not guess
    # away the number: reject this excerpt and select another complete source.
    if re.search(r'\b(?:worldwide|locations|merchants|customers)[1-9](?=[,.;:]|$)', text, re.I):
        return False
    if source_name:
        text = re.sub(r'\s+[-–—|]\s*' + re.escape(source_name) + r'\s*$', '', text, flags=re.I)
    if re.search(r'(?:\.{3}|…|\[\s*…\s*\])\s*[。.!！?？”’"\']*$', text):
        return False
    text = text.rstrip('。.!！?？ ”’"\'')
    return bool(text) and not bool(re.search(
        r'(?:\.{3}|…|\[\s*…\s*\]|read more)$|以.{1,80}为$|(?:用于|包括|以及|基于|关于)$'
        r'|\b(?:is expected to|plans to|according to|including|such as)$', text, re.I))


def chinese_prose(text: str) -> bool:
    # Company names/acronyms may remain Latin. A token "原文" attached to a full
    # English sentence must not qualify it as a Chinese article.
    chinese = len(re.findall(r'[一-鿿]', text))
    latin_words = len(re.findall(r'\b[A-Za-z]{2,}\b', text))
    return chinese >= 2 and chinese >= latin_words


_PRICE_EDITORIAL = re.compile(
    r'which.*(?:stock|buy)|better stock|stock.*(?:to buy|worth buying)|undervalued.*(?:view|compelling)'
    r'|(?:stock|shares?|\([A-Z]+\)).*(?:is up|is down|holds flat|rallies|surges|jumps|slumps|edges? (?:higher|lower))'
    r'|wish you (?:had )?bought|regret not buying|unloved .*stock|别错过|后悔没买'
    r'|哪.*股票|值得买|股价.*(?:上涨|下跌|飙升)', re.I)
_BUSINESS_FACT = re.compile(
    r'\b(?:reported?.*(?:results|earnings|revenue)|earnings|revenue|sales|renew\w*.*(?:licen\w*|agreement)'
    r'|(?:plans?|will|agrees? to) invest|announced|appoint\w*|acqui\w*|merger|launch\w*'
    r'|settlement|lawsuit|litigation|patent verdict|court ruling|appeal|data cent(?:er|re)|cloud.*(?:infrastructure|capacity)|dividend|buyback)\b'
    r'|业绩|营收|利润|投资|发布|任命|续签|收购|并购|结算|分红|回购', re.I)
_RATING_SERVICE = re.compile(
    r"(?:Moody[’']?s|穆迪).*(?:affirms?|upgrades?|downgrades?|cuts?|lifts?|上调|下调|确认|维持).*?(?:ratings?|评级|outlook|展望)", re.I)
_PARTNER_PROMOTION = re.compile(
    r'\b(?:inner circle|partner of the year|partner award|partner status|partner designation)\b'
    r'|合作伙伴(?:奖|称号|认证)|年度合作伙伴|内圈奖', re.I)


def old_event_excerpt(item, text: str) -> bool:
    """Reject explicitly dated old-event investment recaps, not new reporting.

    Anchor to the article's publication date, never today's fetch time. This is
    deliberately limited to leading event dates; historical comparisons remain.
    """
    # A retrospective investment question can recycle an old announcement. Do
    # not apply a two-day stock-news rule to interviews, macro or other feeds.
    if not re.search(r'\b(?:Can|Could|Should|Will)\b.*\?|值得买|增长机会',
                     plain_source(getattr(item, 'title', '')), re.I):
        return False
    published = getattr(item, 'published_at', None)
    if isinstance(published, str):
        try:
            published = datetime.fromisoformat(published)
        except ValueError:
            return False
    if not isinstance(published, datetime):
        return False
    text = plain_source(text)
    if re.search(r'\btoday\b|\byesterday\b|\bwill\b|\bplans? to\b|今日|昨日|今天|昨天|将于|计划于', text, re.I):
        return False
    match = re.match(
        r'^(?:On\s+)?(Jan\w*|Feb\w*|Mar\w*|Apr\w*|May|Jun\w*|Jul\w*|Aug\w*|Sep\w*|Oct\w*|Nov\w*|Dec\w*)'
        r'\.?\s+(\d{1,2})(?:,?\s+(\d{4}))?\s*[,，:：]', text, re.I)
    chinese = re.match(r'^(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日\s*[,，:：]', text)
    if not match and not chinese:
        return False
    if match:
        month = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'].index(match[1][:3].lower()) + 1
        day, year = int(match[2]), match[3]
    else:
        month, day, year = int(chinese[2]), int(chinese[3]), chinese[1]
    # Infer a previous year only around the year boundary, not arbitrary future dates.
    year = int(year) if year else published.year - int(published.month <= 2 and month >= 11)
    try:
        event = date(year, month, day)
    except ValueError:
        return False
    return (published.date() - event).days > 2



def old_event_recap(item) -> bool:
    """Undated continuation clauses must not turn a dated recap into new news."""
    summary = getattr(item, 'summary', '') or getattr(item, 'snippet', '') or ''
    parts = sentences(summary)
    return bool(parts and old_event_excerpt(item, parts[0]) and not re.search(
        r'\btoday\b|\byesterday\b|今日|昨日|今天|昨天', plain_source(summary), re.I))


def factual_excerpt(item) -> str:
    """Prefer a complete operational sentence to an opinion/question headline.

    The immutable title and summary remain attached for context and auditing.
    RSS snippets that merely repeat the headline are not extra evidence.
    """
    if old_event_recap(item):
        return ''
    title = plain_source(getattr(item, 'title', ''))
    summary = getattr(item, 'summary', '') or getattr(item, 'snippet', '')
    eligible = []
    # Mixed headlines often put a buying pitch before a complete reported fact.
    # Extract only that complete fact, without joining clauses or rewriting it.
    title_parts = sentences(title)
    if len(title_parts) > 1 and _PRICE_EDITORIAL.search(title_parts[0]):
        for part in title_parts[1:]:
            if (complete_excerpt(part) and not _PRICE_EDITORIAL.search(part)
                    and re.search(r'\b(?:bought|purchased|acquired|announced|reported)\b|买入|增持|收购|宣布|营收', part, re.I)):
                return part
    for sentence in sentences(summary):
        if (complete_excerpt(sentence) and not old_event_excerpt(item, sentence)
                and len(sentence) >= 30 and _BUSINESS_FACT.search(sentence) and not _PRICE_EDITORIAL.search(sentence)
                and sentence not in title and title not in sentence):
            eligible.append(sentence)
    # When a recap contains an explicit fresh update, lead with that update.
    for sentence in eligible:
        if re.search(r'\btoday\b|\byesterday\b|今日|昨日|今天|昨天', sentence, re.I):
            return sentence
    # Prefer the reported financial result over a sentence merely announcing
    # the earnings date; source-company binding is supplied by the collector.
    for sentence in eligible:
        if re.search(r"earnings|revenue|sales|每股|营收|利润", sentence, re.I) and re.search(r"[$%]|美元|%", sentence):
            return sentence
    if eligible:
        return eligible[0]
    title_sentences = sentences(title)
    if len(title_sentences) == 2 and re.match(r"How we got here|What to know|Here.s why|What.s next|Here.s where (?:the |this )?stock", title_sentences[1], re.I):
        return '' if old_event_excerpt(item, title_sentences[0]) else title_sentences[0]
    # A rolling news-page title is navigation, not a fact. Prefer its actual
    # complete summary sentence even if it is not a company earnings item.
    if re.match(r'Latest .*News and Analysis|.*: Markets Wrap$', title, re.I):
        return next((s for s in sentences(summary) if complete_excerpt(s) and not old_event_excerpt(item, s) and len(s) >= 30), '')
    if re.search(r'\b(?:Can|Should|Could|Will) .*\?|\b(?:Opportunity|Returns)\?', title, re.I):
        # Do not fill a company slot with a question about investment returns.
        return ''
    return '' if old_event_excerpt(item, title) else title


def company_candidate(item, ticker: str) -> bool:
    title = plain_source(getattr(item, 'title', ''))
    text = title + ' ' + plain_source(getattr(item, 'summary', '') or '')
    # A vendor winning a platform's badge is not operating news about that platform.
    # Keep substantive partner contracts, capacity and investments eligible.
    if _PARTNER_PROMOTION.search(title):
        return False
    # A newly published investing commentary can merely recycle last week's
    # launch. Do not turn its undated follow-up clauses into fresh company news.
    if old_event_recap(item):
        return False
    if (ticker == 'MCO' and (_RATING_SERVICE.search(text) or re.search(
            r'(?:upgrad\w*|ratings?|outlook).*\bfrom\s+Moody[’\']?s', title, re.I))
            and not re.search(r'earnings|revenue|profit|营收|盈利|利润|业绩', title, re.I)):
        return False
    return complete_excerpt(factual_excerpt(item), getattr(item, 'source', '')) and (
        not _PRICE_EDITORIAL.search(title) or factual_excerpt(item) != title)


def frontier_candidate(item) -> bool:
    title = plain_source(getattr(item, 'title', ''))
    return not _PRICE_EDITORIAL.search(title) or factual_excerpt(item) != title


def meaningful_quote(item) -> bool:
    text = plain_source(f"{item.title} {item.snippet or ''}")
    generic = re.search(r'great to see|very excited|very exciting|AI is the future|很棒|很兴奋|AI 是未来', text, re.I)
    return not generic or bool(re.search(r'\d|capex|capital spending|billion|million|资本开支|产能|投资额', text, re.I))


def neutral_macro_topic(items, *, supported_text: str = '') -> str:
    from src.processors.macro_topics import macro_topic

    text = supported_text or " ".join(plain_source(item.title) for item in items)
    return macro_topic(text)


def macro_candidate(item) -> bool:
    # Symbolic summit colour is not a market/policy development on its own.
    title = plain_source(item.title)
    if re.search(r"panda diplomacy|熊猫外交", title, re.I):
        return False
    text = title + ' ' + plain_source(getattr(item, 'summary', '') or '')
    local_colour = re.search(r'\b(?:small .*town|village|locals|neighbou?rhood)\b|小镇|村庄|社区居民', title, re.I)
    crime = re.search(r'terror plot|murder|burglary|alleged plot|恐怖阴谋|谋杀|入室盗窃', text, re.I)
    wider_impact = re.search(r'sanctions?|trade|oil|shipping|supply|interest rates?|markets? (?:fall|drop|close)'
                            r'|制裁|贸易|石油|航运|供应|利率|市场(?:下跌|关闭)', text, re.I)
    return not (local_colour and crime and not wider_impact)
