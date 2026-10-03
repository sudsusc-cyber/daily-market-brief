"""Bind the actual source statement to a person, never a search bucket or job.

Role-only headlines can recover by translating an explicitly named statement
from the article. They cannot borrow a byline from an unrelated statement.
"""
import re
from datetime import datetime

from src.processors.news_selection import plain_source

_SPEECH = (r"(?:says?|said|tells?|told|warns?|warned|expects?|expected|believes?|"
           r"argues?|argued|predicts?|predicted|announces?|announced|noted|stated|"
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
    return '(?:' + '|'.join(r'(?<![A-Za-z·])' + re.escape(name) + r'(?![A-Za-z])'
                           for name in sorted(_aliases(person, person_en), key=len, reverse=True)) + ')'


def _named_speech(text, person, person_en=''):
    name = _names(person, person_en)
    # Only grammatical attribution; not a name mentioned as the object of
    # someone else's statement. Parentheses and dated biography are bounded.
    bridge = (r"\s*(?:[（(][^()（）]{1,40}[)）]\s*)?"
              r"(?:,\s*who (?:took office|became (?:CEO|chief executive)) in 20\d{2},\s*)?"
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
        return any(year < published.year for year in years)
    except (TypeError, ValueError):
        return False


def attribution(item, person, person_en='', *, excerpt=None):
    """Replayable proof from the actual claim. No translations or office lookup."""
    fields = [(field, plain_source(getattr(item, field, '') or ''))
              for field in ('title', 'snippet', 'summary', 'source_body')]
    claims = [('excerpt', plain_source(excerpt))] if excerpt is not None else fields
    for field, text in claims:
        if text and _named_speech(text, person, person_en) and not _old_speech(item, text):
            return {'person': person, 'person_en': person_en, 'kind': 'named_speech',
                    'field': 'original_summary' if field in ('snippet', 'summary') else field,
                    'excerpt': text}
    return None


def context_excerpt(item, person, person_en=''):
    """Publish the named source sentence itself, never graft its name onto a title."""
    from src.processors.news_selection import publishable_excerpt, sentences

    for field in ('snippet', 'summary', 'source_body'):
        for text in sentences(plain_source(getattr(item, field, '') or '')):
            if (len(text) <= 1800 and _named_speech(text, person, person_en) and not _old_speech(item, text)
                    and publishable_excerpt(item, text)):
                return text
    return ''


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
