"""Conservative context requirements for ambiguous AI model-use disputes."""
import re

from src.processors.html_safe import strip_all_tags

AI_METHODS = {
    'distillation': r'\bdistill\w*\b|蒸馏',
    'fine_tuning': r'\bfine[- ]tun\w*\b|微调',
}


def needs_technical_context(title: str) -> bool:
    return bool(re.search(r'AI|artificial intelligence|人工智能', title, re.I)
                and re.search(r'model|模型', title, re.I)
                and re.search(r'\b(?:us(?:e|es|ed|ing)|copy\w*|learn\w*)\b|使用|利用|复制|学习', title, re.I)
                and re.search(r'theft|steal\w*|robbery|competition|窃取|盗窃|偷窃|竞争', title, re.I)
                and not any(re.search(p, title, re.I) for p in AI_METHODS.values()))


def _same_speaker(title: str, text: str) -> bool:
    from src.collectors.figures import FIGURES

    speakers = [(cn, en) for cn, _, _, en in FIGURES if cn in title or en.casefold() in title.casefold()]
    if not speakers:
        return True
    # Merely mentioning Huang in somebody else's statement is insufficient.
    return any(re.search(re.escape(name) + r'\s*(?:[（(][^()（）]{1,40}[)）]\s*)?'
                         r'(?:said\b|says\b|called\b|described\b|argued\b|称|表示|认为)', text, re.I)
               for names in speakers for name in names)


def contextual_excerpt(item) -> str:
    """Use one complete paragraph, not a keyword inserted into a vague title."""
    if needs_institution_context(str(getattr(item, 'title', ''))):
        return institution_excerpt(item)
    if not needs_technical_context(str(getattr(item, 'title', ''))):
        return ''
    from src.processors.news_selection import complete_excerpt, plain_source

    for field in ('source_body', 'summary', 'snippet'):
        for paragraph in str(getattr(item, field, '') or '').splitlines():
            text = plain_source(paragraph)
            # Reporter datelines are metadata, not part of the speaker's claim.
            text = re.sub(r'^\[[^\]]{1,100}\]\s*', '', text)
            if (any(re.search(p, text, re.I) for p in AI_METHODS.values())
                    and re.search(r'competition|theft|robbery|竞争|窃取|盗窃', text, re.I)
                    and re.search(r'\b(?:said|says|called|described|argued)\b|称|表示|认为', text, re.I)
                    and _same_speaker(str(getattr(item, 'title', '')), text)
                    and complete_excerpt(text) and len(text) <= 1800):
                return text
    return ''


def context_allows(item, text: str) -> bool:
    if needs_institution_context(str(getattr(item, 'title', ''))):
        return bool(institution_excerpt(item) and strip_all_tags(text).strip() == institution_excerpt(item))
    if not needs_technical_context(str(getattr(item, 'title', ''))):
        return True
    context = contextual_excerpt(item)
    return bool(context and strip_all_tags(text).strip() == context)


def needs_institution_context(title: str) -> bool:
    """Ambiguous institutional acronyms need their actual jurisdiction, not guessing."""
    return bool(re.search(r'\b(?:DOJ|AG|DPA)\b', title)
                and re.search(r'subpoena|investigat|liability|enforcement|诉讼|传票|调查', title, re.I))


def institution_excerpt(item) -> str:
    from src.processors.news_selection import complete_excerpt, plain_source
    from src.processors.translation_guard import _ENTITIES

    title = plain_source(str(getattr(item, 'title', '')))
    if not needs_institution_context(title):
        return ''
    entities = [name for name, aliases in _ENTITIES.items()
                if any(re.search(r'\b' + re.escape(alias) + r'\b', title, re.I) for alias in aliases)]
    for field in ('source_body', 'summary', 'snippet'):
        for paragraph in str(getattr(item, field, '') or '').splitlines():
            text = plain_source(paragraph)
            if (re.search(r'Department of Justice|Attorney General|Data Protection Authority', text, re.I)
                    and re.search(r'subpoena|investigat|liability|enforcement', text, re.I)
                    and (not entities or any(name.casefold() in text.casefold() for name in entities))
                    and complete_excerpt(text) and len(text) <= 1200):
                return text
    # Preserve an independently complete lead instead of guessing the agency in
    # an appended headline clause. No truncation inside a clause is permitted.
    lead, *tail = re.split(r'\s+[—–]\s+', title, maxsplit=1)
    independent_tail = bool(tail and re.match(r'(?:DOJ|AG|DPA)\s+(?:seeks?|requests?|examines?|investigates?)\b', tail[0], re.I)
                            and not re.search(r'\b(?:not|denies?|unconfirmed|retracted|false)\b', tail[0], re.I))
    if independent_tail and not re.search(r'\b(?:DOJ|AG|DPA)\b', lead) and re.search(r'\b(?:subpoenas?|investigates?)\b', lead, re.I) and complete_excerpt(lead):
        return lead
    return ''
