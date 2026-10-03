"""Bind the actual source statement to a person, never a search bucket or job.

Role-only headlines can recover by translating an explicitly named statement
from the article. They cannot borrow a byline from an unrelated statement.
"""
import re
from datetime import UTC, datetime

from src.processors.news_selection import plain_source

_SPEECH = (r"(?:says?|said|tells?|told|warns?|warned|expects?|expected|believes?|"
           r"argues?|argued|predicts?|predicted|announces?|announced|noted|stated|"
           r"describes?|described|characterizes?|characterized|(?:calls?|called)(?=.{0,240}[\"“‘])|"
           r"explains?|explained|adds?|added|称|表示|认为|警告|预计|指出|强调|宣布|说)")
_ROLE = r"(?:CEO|CFO|chief executive(?: officer)?|chairman|chairwoman|president|founder|首席执行官|董事长|总裁|创始人)"
_LOCAL_NAMES = {'纳德拉': '萨提亚·纳德拉', '苏妈': '苏姿丰', '皮叉': '桑达尔·皮查伊',
                '奥特曼': '山姆·奥特曼', '巴菲特': '沃伦·巴菲特'}


def _aliases(person, person_en=''):
    from src.collectors.figures import FIGURES

    names = {person, person_en}
    for cn, _, _, en in FIGURES:
        if person in (cn, en) or (person_en and person_en == en):
            names.update((cn, en, _LOCAL_NAMES.get(cn, '')))
    return [name for name in names if name]


def _names(person, person_en=''):
    # Search aliases such as Buffett/Huang are deliberately not identity proof:
    # Peter Buffett and Andrew Huang are different people.
    return '(?:' + '|'.join(r"(?<![A-Za-z·'-])" + re.escape(name) + r'(?![A-Za-z])'
                           for name in sorted(_aliases(person, person_en), key=len, reverse=True)) + ')'


def _named_speech(text, person, person_en=''):
    name = _names(person, person_en)
    # Only grammatical attribution; not a name mentioned as the object of
    # someone else's statement. Parentheses and dated biography are bounded.
    bridge = (r"\s*(?:[（(][^()（）]{1,40}[)）]\s*)?"
              r"(?:,\s*who (?:took office|became (?:CEO|chief executive)) in 20\d{2},\s*)?"
              r"(?:,\s*(?:[^,.!?]{0,45}" + _ROLE + r"|speaking (?:in|at|during) (?:an? |the )?interview),\s*)?"
              r"(?:(?:于|在)(?:20\d{2}年)?\d{1,2}月\d{1,2}日[，,\s]*)?"
              r"(?:(?:明确|公开|还|也|曾|近日|周二|周三|今日|今日在采访中)\s*|(?:also|has|had|publicly|recently)\s+)*")
    match = (re.search(name + bridge + _SPEECH + r'(?![A-Za-z])', text, re.I)
             or re.search(r'[,，”"]\s*' + _SPEECH + r'\s+(?:' + r'[A-Za-z][A-Za-z0-9.& -]{0,40}\s+' + _ROLE + r'\s+)?' + name, text, re.I))
    if not match:
        return False
    # A denied, hypothetical or unconfirmed attribution is not that person's
    # statement. Negation inside their actual view remains legitimate.
    prefix = re.split(r'[。！？.!?]', text[:match.start()])[-1]
    return not re.search(r'\b(?:denies?|denied|disputed|unconfirmed|falsely|fake|rumou?rs?|if|whether)\b|否认|不实|谣言|传闻|未经证实|如果|假如|是否', prefix, re.I)


def _old_speech(item, text):
    text = re.sub(r',\s*who (?:took office|became (?:CEO|chief executive)) in 20\d{2},', '', text, flags=re.I)
    published = getattr(item, 'published_at', None)
    try:
        published = datetime.fromisoformat(published.replace('Z', '+00:00')) if isinstance(published, str) else published
        if not isinstance(published, datetime):
            return False
        years = [int(year) for year in re.findall(_SPEECH + r'\s+(?:in|during)\s+(20\d{2})\b', text, re.I)]
        years += [int(year) for year in re.findall(r'\b(?:in|during)\s+(20\d{2})\b[^.!?]{0,100}?' + _SPEECH + r'\b', text, re.I)]
        years += [int(year) for year in re.findall(r'(20\d{2})年[^。！？]{0,30}?(?:表示|称|说|警告)', text)]
        if any(year < published.year for year in years):
            return True
        from dateutil.parser import parse

        calendar = r'((?:Jan\w*|Feb\w*|Mar\w*|Apr\w*|May|Jun\w*|Jul\w*|Aug\w*|Sep\w*|Oct\w*|Nov\w*|Dec\w*)\.?\s+\d{1,2}(?:,?\s+20\d{2})?)'
        dates = re.findall(_SPEECH + r'\s+on\s+' + calendar, text, re.I)
        dates += re.findall(r'\bOn\s+' + calendar + r'[^.!?]{0,80}?' + _SPEECH + r'\b', text, re.I)
        for value in dates:
            spoken = parse(value, default=published.replace(month=1, day=1))
            if not 0 <= (published.date() - spoken.date()).days <= 7:
                return True
        for year, month, day in re.findall(r'(?:于|在)(?:(20\d{2})年)?(\d{1,2})月(\d{1,2})日[，,\s]*(?:表示|称|说|警告)', text):
            spoken = published.replace(year=int(year) if year else published.year, month=int(month), day=int(day))
            if not 0 <= (published.date() - spoken.date()).days <= 7:
                return True
        return False
    except (TypeError, ValueError):
        return False


def _surname_unambiguous(raw, full):
    surname = full.rsplit(' ', 1)[1]
    before_name = full[:-len(surname)].strip().casefold()
    connectors = {'said', 'says', 'told', 'warned', 'warns', 'to', 'and', 'but', 'however', 'furthermore', 'while'}
    for match in re.finditer(r'\b' + re.escape(surname) + r'\b', raw, re.I):
        prefix = raw[:match.start()].rstrip(' ')
        if re.search(r"(?<![A-Za-z'-])" + re.escape(before_name) + '$', prefix, re.I):
            continue
        if not prefix or prefix[-1] in '\n,;:!?，；：“”"':
            continue
        token = re.search(r"([A-Za-z][A-Za-z.'-]*)$", prefix)
        if token and token[1].casefold() in connectors:
            continue
        if (prefix.endswith('.') and token and len(token[1]) > 3
                and token[1].casefold() not in {'prof.', 'miss.'}):
            continue  # complete preceding sentence, not A./Jr./Sr./Dr.
        return False
    return True


def source_date_error(item):
    """Return a date-specific reason without conflating it with speaker identity."""
    from src.collectors.news_context import requires_speaker_date

    source_date = getattr(item, 'source_published_at', '')
    if (getattr(item, 'speaker_date_required', False) or requires_speaker_date(item)) and not source_date:
        return 'speaker_date_unverified'
    if source_date:
        try:
            original = datetime.fromisoformat(str(source_date).replace('Z', '+00:00'))
            feed = getattr(item, 'published_at', None)
            feed = datetime.fromisoformat(feed.replace('Z', '+00:00')) if isinstance(feed, str) else feed
            if original.tzinfo is None or not isinstance(feed, datetime) or feed.tzinfo is None:
                return 'speaker_date_invalid'
            if not 0 <= (feed.astimezone(UTC).date() - original.astimezone(UTC).date()).days <= 7:
                return 'speaker_date_outside_window'
        except (TypeError, ValueError):
            return 'speaker_date_invalid'
    return ''


def attribution(item, person, person_en='', *, excerpt=None):
    """Replayable proof from the actual claim. No translations or office lookup."""
    if source_date_error(item):
        return None
    fields = [(field, plain_source(getattr(item, field, '') or ''))
              for field in ('title', 'snippet', 'summary', 'source_body')]
    claims = [('excerpt', plain_source(excerpt))] if excerpt is not None else fields
    for field, text in claims:
        if text and _named_speech(text, person, person_en) and not _old_speech(item, text):
            return {'person': person, 'person_en': person_en, 'kind': 'named_speech',
                    'field': 'original_summary' if field in ('snippet', 'summary') else field,
                    'excerpt': text}
        # Newspaper prose normally introduces a full name once and then uses a
        # surname. Resolve it only within this source, with no competing person
        # of that surname; never use the search alias or current office-holder.
        if not text or _old_speech(item, text):
            continue
        raw = '\n'.join(value for _, value in fields)
        for full in _aliases(person, person_en):
            if not full.isascii() or ' ' not in full:
                continue
            surname = full.rsplit(' ', 1)[1]
            if not _named_speech(text, surname):
                continue
            introductions = [sentence for sentence in re.split(r'[\n!?]|(?<=\.)\s+(?=[A-Z])', raw)
                             if _named_speech(sentence, full)
                             or re.search(_ROLE + r'\s+' + re.escape(full) + r'\b', sentence, re.I)
                             or re.search(re.escape(full) + r',\s*[^,.!?]{0,45}' + _ROLE + r'\s*[,。.]', sentence, re.I)]
            if not introductions:
                continue
            if not _surname_unambiguous(raw, full):
                continue
            return {'person': person, 'person_en': person_en, 'kind': 'resolved_surname',
                    'field': 'excerpt', 'excerpt': text, 'identity_excerpt': introductions[0], 'full_name': full}
    return None


def context_excerpt(item, person, person_en=''):
    """Publish the named source sentence itself, never graft its name onto a title."""
    from src.processors.news_selection import publishable_excerpt, sentences

    candidates = []
    for field in ('snippet', 'summary', 'source_body'):
        for text in sentences(plain_source(getattr(item, field, '') or '')):
            if (len(text) <= 1800 and attribution(item, person, person_en, excerpt=text)
                    and publishable_excerpt(item, text)):
                candidates.append(text)
    # Topic overlap ranks already-bound statements; it never proves identity,
    # equivalence or truth and never rewrites the sentence.
    words = set(re.findall(r'[a-z]{4,}', plain_source(getattr(item, 'title', '')).lower()))
    return max(candidates, key=lambda text: len(words & set(re.findall(r'[a-z]{4,}', text.lower()))), default='')


def needs_speaker_context(item, person, person_en=''):
    from src.collectors.figures import _FINNHUB_FALLBACK_ALIASES, FIGURES

    title = plain_source(getattr(item, 'title', ''))
    surnames = next((_FINNHUB_FALLBACK_ALIASES.get(cn, ()) for cn, _, _, en in FIGURES
                     if person in (cn, en) or person_en == en), ())
    surname_speech = any(' ' not in name and re.search(r'\b' + re.escape(name) + r'\s+' + _SPEECH, title, re.I)
                         for name in surnames)
    return (not attribution(item, person, person_en, excerpt=title)
            and (surname_speech or bool(re.search(_ROLE + r'\s+' + _SPEECH, title, re.I))
                 or bool(re.match(_names(person, person_en) + r'\s*[:：]', title, re.I))))
