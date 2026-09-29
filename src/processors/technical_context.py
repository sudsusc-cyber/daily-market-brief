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
    if not needs_technical_context(str(getattr(item, 'title', ''))):
        return True
    context = contextual_excerpt(item)
    return bool(context and strip_all_tags(text).strip() == context)
