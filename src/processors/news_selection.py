"""Reject reproducible editorial noise before asking a model to rank news."""

import html
import re
from datetime import date, datetime

from src.processors.editorial_evidence import editorial_issue, status_has_scope
from src.processors.html_safe import strip_all_tags
from src.processors.technical_context import (
    context_allows,
    contextual_excerpt,
    needs_technical_context,
)

# RSS sometimes joins a promotional standfirst directly to a wire dateline.
# The reporting sentence after the dateline remains an exact source substring.
_WIRE_DATELINE = re.compile(
    r'(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?'
    r'|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)'
    r'\.?\s+\d{1,2},?\s+\d{4}\s*(?:\((?:GLOBE NEWSWIRE|BUSINESS WIRE|PR NEWSWIRE)\))?'
    r'\s*(?:--|—{1,2})\s*', re.I)


def reporting_text(text: str) -> str:
    """Drop a wire dateline/standfirst, never rewrite the reporting sentence."""
    text = strip_source_prefix(plain_source(text))
    dateline = _WIRE_DATELINE.search(text[:600])
    if dateline and re.search(
            r"(?:^|(?<=[a-z]))[A-Z][A-Z .'-]{1,45},\s*(?:[A-Z][A-Za-z .'-]{1,30},\s*)?$",
            text[:dateline.start()]):
        return text[dateline.end():].strip()
    return text


def strip_source_prefix(text: str) -> str:
    """Recognize a domain masthead by delimiter syntax, never prose attribution."""
    return re.sub(r"^(?:https?://)?(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?:/[\w/-]*)?\s*(?:--|—|–|\|)\s*", "", text).strip()


def plain_source(text: str) -> str:
    # Decode before stripping, then escape only once at final HTML construction.
    for _ in range(2):
        text = html.unescape(text or '')
    return strip_all_tags(text).strip()


def sentences(text: str) -> list[str]:
    text = reporting_text(text)
    # Split only where the next sentence begins; keep decimal points, initials,
    # month abbreviations and company suffixes within their complete sentence.
    return [s.strip() for s in re.split(r"(?<=[。！？])|(?<=[.!?])\s+(?=[A-Z])|\s+[—–]\s+(?=Here[’']s\b)", text) if s.strip()]


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
    r'(?:stock|shares?).*(?:best|worst|strongest|weakest).*(?:day|week|month|quarter|year)|股价.*(?:最佳|最差|最好|最坏).*表现'
    r'|which.*(?:stock|buy)|better stock|stock.*(?:to buy|worth buying)|undervalued.*(?:view|compelling)'
    r'|(?:stock|shares?|\([A-Z]+\)).*(?:is up|is down|holds flat|rises?|falls?|rall(?:y|ies)|surges?|jumps?|slumps?|edges? (?:higher|lower))'
    r'|wish you (?:had )?bought|regret not buying|unloved .*stock|别错过|后悔没买'
    r'|哪.*股票|值得买|股价.*(?:上涨|下跌|飙升)', re.I)
_BUSINESS_FACT = re.compile(
    r'\b(?:reported?.*(?:results|earnings|revenue)|earnings|revenue|sales|renew\w*.*(?:licen\w*|agreement)'
    r'|(?:plans?|will|agrees? to) invest|announced|introduc\w*|appoint\w*|acqui\w*|merger|launch\w*'
    r'|(?:paus\w*|cancel\w*).*(?:training|evaluation|launch)|settlement|lawsuit|litigation|patent verdict|court ruling|appeal|data cent(?:er|re)|cloud.*(?:infrastructure|capacity)|dividend|buyback)\b'
    r'|业绩|营收|利润|投资|发布|任命|续签|收购|并购|结算|分红|回购', re.I)
_RATING_SERVICE = re.compile(
    r"(?:Moody[’']?s|穆迪).*(?:affirms?|upgrades?|downgrades?|cuts?|lifts?|上调|下调|确认|维持).*?(?:ratings?|评级|outlook|展望)", re.I)
_PARTNER_PROMOTION = re.compile(
    r'\b(?:inner circle|partner of the year|partner award|partner status|partner designation)\b'
    r'|合作伙伴(?:奖|称号|认证)|年度合作伙伴|内圈奖', re.I)

# Price colour or an award in the same headline must not erase an actual
# operating announcement. Require an event/metric relation, not a company name.
_OPERATING_EVENT = re.compile(
    r'\b(?:reports?|reported|raises?|raised|cuts?|cut|announces?|announced)\b.{0,60}'
    r'\b(?:earnings|revenue|profit|guidance|dividend|buyback|acquisition)\b|'
    r'\b(?:signs?|signed|renews?|renewed|wins?|won)\b.{0,45}\b(?:contract|agreement)\b|'
    r'(?:公布|发布|上调|下调).{0,20}(?:业绩|营收|利润|指引|分红)|'
    r'(?:签署|续签|获得).{0,20}(?:合同|协议)', re.I)



_PROMO_PROSE = re.compile(
    r"^move over[,，]|^让开[，,]|^why (?:it|this|that) (?:could|may|might|matters)|为何.*(?:股票|重要)|为什么.*(?:股票|重要)", re.I)
_ROUNDUP = re.compile(r"回购(?:集合|汇总|一览)|(?:buyback|repurchase).*(?:roundup|round-up|round up)", re.I)


def promotional_prose(text: str) -> bool:
    return any(_PROMO_PROSE.search(part) for part in sentences(text))


def company_fact_matches(text: str, ticker: str) -> bool:
    """Roundup titles are not a single holding's report. Keep a scoped sentence."""
    if _ROUNDUP.search(plain_source(text)):
        return False
    from src.collectors.company_news import _RELEVANCE_KEYWORDS
    from src.config import HOLDINGS

    aliases = list(_RELEVANCE_KEYWORDS.get(ticker, []))
    aliases.extend(h.name for h in HOLDINGS if h.ticker == ticker)
    aliases.extend({'0700.HK': ['腾讯', 'Tencent'], '9992.HK': ['泡泡玛特', 'POP MART']}.get(ticker, []))
    scope = re.split(r'其中|among them', plain_source(text), flags=re.I)
    if len(scope) > 1 and not any(scope[-1].strip().lower().startswith(alias.strip().lower()) for alias in aliases):
        return False
    return bool(aliases and any(plain_source(text).lower().startswith(alias.strip().lower()) for alias in aliases))


def holding_in_excerpt(text: str, ticker: str) -> bool:
    """A published passage must itself name its assigned holding."""
    from src.collectors.company_news import _RELEVANCE_KEYWORDS
    from src.processors.presentation_vocabulary import COMPANY_DISPLAY_NAMES

    aliases = [*_RELEVANCE_KEYWORDS.get(ticker, []), COMPANY_DISPLAY_NAMES.get(ticker, '')]
    return any(alias.strip() and re.search(
        (r"(?<![A-Za-z0-9])" if re.match(r"[A-Za-z0-9]", alias.strip()) else '')
        + re.escape(alias.strip())
        + (r"(?![A-Za-z0-9])" if re.search(r"[A-Za-z0-9]$", alias.strip()) else ''), text, re.I)
        for alias in aliases)


def publication_candidates(item, text: str) -> list[str]:
    """Include adjacent evidence so a financing's investor isn't lost."""
    parts = sentences(text)
    ticker = getattr(item, 'holding_ticker', None)
    result = list(parts)
    if ticker:
        for left, right in zip(parts, parts[1:], strict=False):
            if (not holding_in_excerpt(left, ticker) and holding_in_excerpt(right, ticker)
                    and re.search(r"participat|invest|led by|partner|agreement|supply|customer|参与|投资|领投|合作|供应|客户", right, re.I)
                    and re.search(r"round|financing|deal|transaction|agreement|contract|project|Series|本轮|此次|该|这", right, re.I)
                    and len(left + right) <= 1200):
                # Keep the exact source interval, including its separator.
                start = text.find(left)
                end = text.find(right, start + len(left))
                if start >= 0 and end >= 0:
                    result.insert(0, text[start:end + len(right)])
    return result


def roundup_excerpt(item, ticker: str) -> str:
    summary = getattr(item, 'summary', '') or getattr(item, 'snippet', '') or ''
    return next((part for part in sentences(summary)
                 if company_fact_matches(part, ticker) and complete_excerpt(part)
                 and _BUSINESS_FACT.search(part)), '')


def explicit_old_event(item, text: str, *, max_age_days: int = 2, allow_fresh_update: bool = True) -> bool:
    """Check calendar dates attached to completed events, not comparison baselines.

    Only same-clause event/date bindings are considered. A publication timestamp
    is never substituted for an event date; fiscal periods and 'since' records
    do not date the reporting event. Undated claims remain subject to other gates.
    """
    published = getattr(item, 'published_at', None)
    try:
        published = datetime.fromisoformat(published.replace('Z', '+00:00')) if isinstance(published, str) else published
    except ValueError:
        return False
    if not isinstance(published, datetime):
        return False
    completed = r"\b(?:completed|closed|acquired|announced|launched|released|published|reported|signed|approved|appointed|resigned|opened|began)\b|完成|收购|宣布|发布|公布|签署|批准|任命|辞任|开业|启动"
    calendar = (r"(?P<month>Jan\w*|Feb\w*|Mar\w*|Apr\w*|May|Jun\w*|Jul\w*|Aug\w*|Sep\w*|Oct\w*|Nov\w*|Dec\w*)\.?\s+(?P<day>\d{1,2})(?:,?\s+(?P<year>20\d{2}))?"
                r"|(?:(?P<zyear>20\d{2})\s*年)?\s*(?P<zmonth>\d{1,2})\s*月\s*(?P<zday>\d{1,2})\s*日"
                r"|(?P<iso>20\d{2}-\d{2}-\d{2})")
    old_event = fresh_event = False
    for part in sentences(text):
        for clause in re.split(r"[;；]|\b(?:but|while|whereas)\b|但是|而今天", part, flags=re.I):
            if not re.search(completed, clause, re.I):
                continue
            dated_event = False
            for match in re.finditer(calendar, clause, re.I):
                before, after = clause[:match.start()], clause[match.end():]
                # Historical baselines and financial measurement periods are
                # not the date of the announcement/acquisition/etc.
                if re.search(r"(?:since|from|compared (?:with|to)|as of|ended|ending|截至|自|相比)\s*$", before, re.I):
                    continue
                date_bound = bool(re.search(r"(?:on|in|于|在)\s*$", before, re.I)
                                  or not before.strip()
                                  or re.search(r"(?:released|published|reported)\s*$", before, re.I))
                if not date_bound or re.match(r"\s*(?:以来|之前|以后|之后)", after):
                    continue
                try:
                    if match['iso']:
                        event = date.fromisoformat(match['iso'])
                    else:
                        month = (['jan','feb','mar','apr','may','jun','jul','aug','sep','oct','nov','dec'].index(match['month'][:3].lower()) + 1
                                 if match['month'] else int(match['zmonth']))
                        year = match['year'] or match['zyear']
                        year = int(year) if year else published.year - int(published.month <= 2 and month >= 11)
                        event = date(year, month, int(match['day'] or match['zday']))
                    dated_event = True
                    age = (published.date() - event).days
                    old_event |= age > max_age_days
                    fresh_event |= 0 <= age <= max_age_days
                except ValueError:
                    continue
            if (not dated_event and re.search(r"\btoday\b|\byesterday\b|今日|昨日|今天|昨天", clause, re.I)
                    and not re.search(r"\b(?:recalled|reiterated|reviewed)\b|回顾|重申", clause, re.I)):
                fresh_event = True
    return old_event and not (allow_fresh_update and fresh_event)


def old_event_excerpt(item, text: str) -> bool:
    return explicit_old_event(item, plain_source(text))


def old_event_recap(item) -> bool:
    """Undated continuation clauses must not turn a dated recap into new news."""
    summary = getattr(item, 'summary', '') or getattr(item, 'snippet', '') or ''
    parts = sentences(summary)
    return bool(parts and old_event_excerpt(item, parts[0]) and old_event_excerpt(item, summary))


def undated_immediate_leadership_change(item, text: str) -> bool:
    """A repost timestamp does not date an immediately-effective succession.

    Require an explicit calendar date in the original reporting context; the
    adjacent event sentence must carry it, or a dated wire release must supply
    its dateline. Never accept the collector's fetch/publication timestamp alone.
    """
    if not (re.search(r"effective immediately|即刻生效|立即生效|即时生效", text, re.I)
            and re.search(r"chair(?:man|person)?|\bCEO\b|chief executive|董事长|首席执行官", text, re.I)
            and re.search(r"stepped down|resign|appoint|elect|named|卸任|辞任|任命|当选|出任|接任", text, re.I)):
        return False
    original = plain_source(str(getattr(item, 'summary', '') or getattr(item, 'snippet', '') or ''))
    title = plain_source(str(getattr(item, 'title', '') or ''))
    calendar = (r"(?P<month>Jan\w*|Feb\w*|Mar\w*|Apr\w*|May|Jun\w*|Jul\w*|Aug\w*|Sep\w*|Oct\w*|Nov\w*|Dec\w*)\.?\s+(?P<day>\d{1,2}),?\s+(?P<year>20\d{2})"
                r"|(?P<zyear>20\d{2})\s*年\s*(?P<zmonth>\d{1,2})\s*月\s*(?P<zday>\d{1,2})\s*日")
    parts = [part for part in [title, *sentences(original)]
             if re.search(r"effective immediately|即刻生效|立即生效|即时生效", part, re.I)]
    dateline = _WIRE_DATELINE.search(original[:600])
    if dateline:
        parts.append(dateline[0])
    published = getattr(item, 'published_at', None)
    try:
        published = datetime.fromisoformat(published.replace('Z', '+00:00')) if isinstance(published, str) else published
        if not isinstance(published, datetime):
            return True
        event_dates = []
        for part in parts:
            for match in re.finditer(calendar, part, re.I):
                if match['year']:
                    month = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'].index(match['month'][:3].lower()) + 1
                    event_dates.append(date(int(match['year']), month, int(match['day'])))
                else:
                    event_dates.append(date(int(match['zyear']), int(match['zmonth']), int(match['zday'])))
        # Ambiguous multiple dates need a better source, not a guessed event date.
        return len(set(event_dates)) != 1 or not 0 <= (published.date() - event_dates[0]).days <= 2
    except (ValueError, TypeError):
        return True


def publishable_excerpt(item, text: str) -> bool:
    return ((not getattr(item, 'holding_ticker', None) or holding_in_excerpt(text, item.holding_ticker))
            and context_allows(item, text) and complete_excerpt(text, getattr(item, 'source', ''))
            and not editorial_issue(text) and not promotional_prose(text)
            and not old_event_excerpt(item, text) and not undated_immediate_leadership_change(item, text + ' ' + plain_source(str(getattr(item, 'title', ''))) + ' ' + plain_source(str(getattr(item, 'summary', '') or getattr(item, 'snippet', '') or '')))
            and status_has_scope(item, text))


def factual_excerpt(item) -> str:
    """Prefer a complete operational sentence to an opinion/question headline.

    The immutable title and summary remain attached for context and auditing.
    RSS snippets that merely repeat the headline are not extra evidence.
    """
    if needs_technical_context(str(getattr(item, 'title', ''))):
        return contextual_excerpt(item)
    if old_event_recap(item):
        return ''
    title = plain_source(getattr(item, 'title', ''))
    if _ROUNDUP.search(title):
        return roundup_excerpt(item, getattr(item, 'holding_ticker', '') or '')
    summary = getattr(item, 'summary', '') or getattr(item, 'snippet', '')
    eligible = []
    # Mixed headlines often put a buying pitch before a complete reported fact.
    # Extract only that complete fact, without joining clauses or rewriting it.
    title_parts = sentences(title)
    if promotional_prose(title):
        return next((part for part in title_parts if not promotional_prose(part)
                     and publishable_excerpt(item, part)
                     and (_BUSINESS_FACT.search(part) or re.search(
                         r'go public|IPO|fil(?:e|ed|ing)|上市|招股|提交', part, re.I))), '')
    if len(title_parts) > 1 and _PRICE_EDITORIAL.search(title_parts[0]):
        for part in title_parts[1:]:
            if (complete_excerpt(part) and not _PRICE_EDITORIAL.search(part)
                    and re.search(r'\b(?:bought|purchased|acquired|announced|reported)\b|买入|增持|收购|宣布|营收', part, re.I)):
                return part
    for sentence in publication_candidates(item, plain_source(summary)):
        if (publishable_excerpt(item, sentence)
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
    if len(title_sentences) == 2 and re.match(r"How we got here|What to know|Here.s (?:why|the|what)|What.s next|Here.s where (?:the |this )?stock", title_sentences[1], re.I):
        return title_sentences[0] if publishable_excerpt(item, title_sentences[0]) else ''
    # A rolling news-page title is navigation, not a fact. Prefer its actual
    # complete summary sentence even if it is not a company earnings item.
    if re.match(r'Latest .*News and Analysis|.*: Markets Wrap$', title, re.I):
        return next((s for s in sentences(summary) if complete_excerpt(s) and not old_event_excerpt(item, s) and len(s) >= 30), '')
    if re.search(r'\b(?:Can|Should|Could|Will) .*\?|\b(?:Opportunity|Returns)\?', title, re.I):
        # Do not fill a company slot with a question about investment returns.
        return ''
    return title if publishable_excerpt(item, title) else ''


def company_candidate(item, ticker: str) -> bool:
    title = plain_source(getattr(item, 'title', ''))
    text = title + ' ' + plain_source(getattr(item, 'summary', '') or '')
    if re.search(r"\b(?:net worth|wealth.*shares|richest|biography)\b", title, re.I) and re.search(r"\?|profile|who is|what is", title, re.I):
        return False
    # Appointment beneficiary is the grammatical subject, not the appointee's
    # existing employer. Apply to all configured issuers via collector aliases.
    appointment = re.match(r"(.+?)\s+appoints?\s+.+?\s+to\s+(?:its\s+)?(?:board|board of directors)", title, re.I)
    if appointment and not company_fact_matches(appointment[1], ticker):
        return False
    if _ROUNDUP.search(title):
        return bool(roundup_excerpt(item, ticker))
    excerpt = factual_excerpt(item)
    # An analysis headline is not rescued by another metaphorical fragment.
    # A complete operating announcement in the body remains eligible.
    if editorial_issue(title) == 'valuation_or_editorial_opinion' and not _OPERATING_EVENT.search(excerpt):
        return False
    # A vendor winning a platform's badge is not operating news about that platform.
    # Keep substantive partner contracts, capacity and investments eligible.
    if _PARTNER_PROMOTION.search(title) and not _OPERATING_EVENT.search(title):
        return False
    # A newly published investing commentary can merely recycle last week's
    # launch. Do not turn its undated follow-up clauses into fresh company news.
    if old_event_recap(item):
        return False
    if (ticker == 'MCO' and (_RATING_SERVICE.search(text) or re.search(
            r'(?:upgrad\w*|ratings?|outlook).*\bfrom\s+Moody[’\']?s', title, re.I))
            and not re.search(r'earnings|revenue|profit|营收|盈利|利润|业绩', title, re.I)):
        return False
    return publishable_excerpt(item, factual_excerpt(item)) and (
        not _PRICE_EDITORIAL.search(title) or factual_excerpt(item) != title or bool(_OPERATING_EVENT.search(title)))


def frontier_candidate(item) -> bool:
    title = plain_source(getattr(item, 'title', ''))
    excerpt = factual_excerpt(item)
    return publishable_excerpt(item, excerpt) and (
        not _PRICE_EDITORIAL.search(title) or excerpt != title)


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
    text = title + ' ' + plain_source(getattr(item, 'summary', '') or '')
    local_colour = re.search(r'\b(?:small .*town|village|locals|neighbou?rhood)\b|小镇|村庄|社区居民', title, re.I)
    crime = re.search(r'terror plot|murder|burglary|alleged plot|恐怖阴谋|谋杀|入室盗窃', text, re.I)
    wider_impact = re.search(r'sanctions?|trade|oil|shipping|supply|interest rates?|markets? (?:fall|drop|close)'
                            r'|制裁|贸易|石油|航运|供应|利率|市场(?:下跌|关闭)', text, re.I)
    symbolic_colour = re.search(r"panda diplomacy|熊猫外交", title, re.I)
    return not ((local_colour and crime or symbolic_colour) and not wider_impact)
