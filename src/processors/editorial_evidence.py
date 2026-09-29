"""Bounded publication checks beyond a valid link or a complete sentence.

These checks reject observable editorial/scope defects. They do not claim to
independently verify every report or replace human fact checking.
"""
import re
from urllib.parse import urlsplit


def editorial_issue(text: str) -> str | None:
    text = text.strip()
    if re.search(r'[?？](?:[”\"\']|\s*[-—|].*)?$', text):
        return 'question_not_event'
    if re.match(r'(?:this|that|these|those|it|its)\b|此次|这次|上述|该举措|这一(?:发布|举措|增长)|此举', text, re.I):
        return 'unresolved_context'
    if re.search(r'analyst blog (?:highlights?|mentions?)|分析师博客.*(?:提及|关注)|博客重点提及', text, re.I):
        return 'media_roundup'
    if re.search(r'(?:for|over|than) (?:\d+|a hundred) years|已(?:有|超过).*年', text, re.I) and re.search(
            r'rated debt|评级债务|hundred|百年', text, re.I):
        return 'historical_background'
    if re.search(r'(?:makes? (?:a )?major push into|大举进军).*market|大举进军.*市场', text, re.I):
        return 'promotional_context_missing'
    if re.search(r'provide\w*.*(?:revenue )?visibility|提供.*(?:收入|营收)可见性|'
                 r'positioned to benefit|evergreen investments|competitive assets?|'
                 r'highlights?.*effort|凸显.*努力', text, re.I):
        return 'investment_opinion'
    return None


def analysis_source(title: str, summary: str = '') -> bool:
    return bool(re.search(
        r'\b(?:buy before|stock is a buy|investment case|analyst blog|here.s why.*moat|'
        r'political asset|wish you.*bought)\b|投资逻辑|值得买|分析师博客', title + ' ' + summary, re.I))


def model_status_claim(text: str) -> bool:
    return bool(re.search(r'OpenAI|Anthropic|GPT[- .]?\d|模型|model', text, re.I) and re.search(
        r'\b(?:paus\w*|cancel\w*|scrap\w*|halt\w*|suspend\w*|abandon\w*|axes?|shelv\w*|postpon\w*|delay\w*)\b|暂停|取消|搁置|终止|砍掉|放弃|推迟|延后', text, re.I))


def status_has_scope(item, excerpt: str) -> bool:
    """Do not infer a product shutdown from an aggregator's headline.

    Even official headlines must name the affected activity. Third-party text
    additionally needs a complete non-headline reporting sentence in the body.
    No trust is granted by an unverified source label or source_type alone.
    """
    if not model_status_claim(excerpt):
        return True
    scope = r'\b(?:training|evaluation|inference|deployment|rollout|launch|release|API|service|subscriptions?)\b|训练|评估|推理|部署|发布|上线|服务|订阅'
    if not re.search(scope, excerpt, re.I):
        return False
    import html

    from src.processors.html_safe import strip_all_tags
    body = strip_all_tags(html.unescape(str(getattr(item, 'summary', '') or getattr(item, 'snippet', '') or '')))
    title = str(getattr(item, 'title', '') or '')
    host = (urlsplit(str(getattr(item, 'url', '') or '')).hostname or '').lower()
    official = any(host == domain or host.endswith('.' + domain) for domain in ('openai.com', 'anthropic.com'))
    return official or (excerpt in body and excerpt != title and len(excerpt) >= 50)
