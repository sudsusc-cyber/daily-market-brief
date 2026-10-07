"""Canonical macro topics: classification arranges facts, never rewrites them."""

import re


def _has(pattern: str, text: str) -> bool:
    return bool(re.search(pattern, text, re.I))


def macro_topic(text: str) -> str:
    from src.processors.macro_events import extract_event
    return extract_event(text).topic


def macro_topics(texts: list[str]) -> list[str]:
    from src.processors.macro_events import edition_events
    return [event.topic for event in edition_events(texts)]


# Editorial fallback priorities: economy-wide policy/data and systemic events
# outrank sector stories. Model order breaks ties, never publisher/feed order.
_MACRO_PRIORITY = {
    '货币政策': 100, '通胀数据': 95, '就业市场': 95, '经济增长': 95,
    '美债市场': 90, '中美关系': 90, '中东局势': 90, '俄乌局势': 90,
    '国际贸易': 85, '财政政策': 85, '信用市场': 80, '能源市场': 80,
    '贵金属市场': 75, '债券市场': 75, '外汇市场': 75, '中国经济': 75, '资本流动': 70,
    '科技监管': 75, '地缘政治': 65, 'AI 安全': 60, '国防开支': 55,
    '选举与司法': 50, '资本市场': 45,
    '公共卫生': 60,
}


def macro_importance(topic: str, facts: list[str]) -> int:
    """Rank verified facts only; do not let model-added urgency affect selection."""
    score = _MACRO_PRIORITY.get(topic, 0)
    text = ' '.join(facts)
    if _has(r'系统性风险|金融危机|银行挤兑|主权违约|systemic risk|financial crisis|bank run|sovereign default', text):
        score = max(score, 110)
    # Analysis/commentary is below a contemporaneous decision or data release.
    if all(_has(r'评论|通讯|newsletter|opinion|commentary', fact) for fact in facts):
        score -= 30
    if all(_has(r'小镇|当地居民|small town|small English town|local residents', fact) for fact in facts):
        score -= 40
    return score
