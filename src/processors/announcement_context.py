"""Evidence requirements for company actions presented inside market recaps.

A market-wrap date dates the wrap, not the announcement. No issuer-specific
exception or model-written event date can satisfy this contract.
"""
import re
from datetime import date, datetime

from src.processors.event_semantics import has_event

_RECAP = re.compile(
    r"stock market today|today['’]s (?:stock |market )|market(?:s)? (?:wrap|recap|roundup|round-up)"
    r"|daily (?:market |stock )?roundup|今日股市|股市今日|市场(?:综述|回顾)|每日(?:股市|市场)", re.I)
_ACTION_OBJECT = re.compile(
    r"buyback|repurchas|dividend|acquisition|merger|takeover|licen[cs]|contract|回购|分红|股息|收购|并购|授权|合同", re.I)
_MONEY = re.compile(r"\$\s*\d|\d[\d.,]*\s*(?:billion|million|亿美元|万美元|亿港元|美元|港元)", re.I)
_CALENDAR = re.compile(
    r"\b(?P<month>Jan\w*|Feb\w*|Mar\w*|Apr\w*|May|Jun\w*|Jul\w*|Aug\w*|Sep\w*|Oct\w*|Nov\w*|Dec\w*)\.?\s+(?P<day>\d{1,2}),?\s+(?P<year>20\d{2})\b"
    r"|(?P<zyear>20\d{2})\s*年\s*(?P<zmonth>\d{1,2})\s*月\s*(?P<zday>\d{1,2})\s*日"
    r"|(?P<iso>20\d{2}-\d{2}-\d{2})")


def calendar_dates(text: str) -> list[date]:
    dates = set()
    for match in _CALENDAR.finditer(text):
        try:
            if match['iso']:
                value = date.fromisoformat(match['iso'])
            elif match['year']:
                month = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'].index(match['month'][:3].lower()) + 1
                value = date(int(match['year']), month, int(match['day']))
            else:
                value = date(int(match['zyear']), int(match['zmonth']), int(match['zday']))
            dates.add(value)
        except ValueError:
            continue
    return sorted(dates)


def requires_action_context(item) -> bool:
    title = str(getattr(item, 'title', ''))
    return bool(_RECAP.search(title) and _ACTION_OBJECT.search(title))


def action_context(item) -> str:
    """Require an exact dated reporting sentence, including its amount scope.

    Generic confidence/market-colour copy cannot rescue a recap headline. Any
    enrichment must already be fetched and identity-bound by the source layer.
    """
    from src.processors.news_selection import (
        complete_excerpt,
        holding_in_excerpt,
        plain_source,
        sentences,
    )

    if not requires_action_context(item):
        return ''
    published = getattr(item, 'published_at', None)
    try:
        published = datetime.fromisoformat(published.replace('Z', '+00:00')) if isinstance(published, str) else published
        if not isinstance(published, datetime):
            return ''
    except ValueError:
        return ''
    for field in ('source_body', 'summary', 'snippet'):
        for sentence in sentences(plain_source(getattr(item, field, '') or '')):
            dates = calendar_dates(sentence)
            if (len(dates) != 1 or not 0 <= (published.date() - dates[0]).days <= 1
                    or not _ACTION_OBJECT.search(sentence) or not complete_excerpt(sentence)
                    or not has_event(sentence, 'approval', 'announcement', 'completion', 'acquisition')
                    or (_MONEY.search(item.title) and not _MONEY.search(sentence))):
                continue
            ticker = getattr(item, 'holding_ticker', None)
            if ticker and not holding_in_excerpt(sentence, ticker):
                continue
            # Do not accept merely a navigation date at the beginning of a wrap.
            if _RECAP.search(sentence):
                continue
            if (re.search(r"buyback|repurchas|回购", sentence, re.I)
                    and re.search(r"\btotal\b|总额|合计", sentence, re.I)
                    and not re.search(r"remaining|amount authorized|authorization|cumulative|剩余|授权额度|累计已", sentence, re.I)):
                continue
            return sentence
    return ''


def action_context_date(item, excerpt: str) -> str:
    if requires_action_context(item) and excerpt == action_context(item):
        dates = calendar_dates(excerpt)
        return dates[0].isoformat() if len(dates) == 1 else ''
    return ''
