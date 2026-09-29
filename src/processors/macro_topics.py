"""Canonical macro topics: classification arranges facts, never rewrites them."""

import re


def _has(pattern: str, text: str) -> bool:
    return bool(re.search(pattern, text, re.I))


_CHINA = r'China|Chinese|Xi Jinping|中国|中方|对华|习近平'
_US = r'\bUS\b(?!\$|\s*dollars?)|U\.S\.(?!\s*dollars?)|United States|America\w*|Trump|美国|美方|特朗普'
_MIDDLE_EAST = r'Iran|Saudi|Israel|Gaza|Hormuz|Middle East|伊朗|沙特|以色列|加沙|霍尔木兹|中东|美伊|红海'
_US_TREASURY = r'Treasuries|Treasury (?:yields?|bonds?|debt|selloff)|U\.?S\.? (?:government )?(?:bonds?|yields?|debt)|美债|美国国债'


def macro_topic(text: str) -> str:
    """One canonical label per fact, with specific subjects before broad keywords.

    Treasury-market news remains in 美债市场 even when oil or tariffs explain
    the move. Regional oil, airline and nuclear stories share 中东局势.
    """
    if _has(_US_TREASURY, text):
        return '美债市场'
    if _has(r'中美|美中|Sino[ -]American|U\.?S\.?[ -]China|China[ -]U\.?S\.?', text) or (
            _has(_CHINA, text) and _has(_US, text)):
        return '中美关系'
    if _has(_MIDDLE_EAST, text):
        return '中东局势'
    if _has(r'Russia|Ukraine|俄乌|俄罗斯|乌克兰', text):
        return '俄乌局势'
    for topic, pattern in (
        ('国防开支', r'(?:missile|defen[cs]e|military|导弹|国防|军工).*(?:contract|spending|合同|开支)'
         r'|(?:contract|spending|合同|开支).*(?:missile|defen[cs]e|military|导弹|国防|军工)'),
        ('能源市场', r'\boil\b|\bcrude\b|\benergy\b|natural gas|油价|原油|石油|能源|天然气'),
        ('财政政策', r'\btax\w*\b|\bIRS\b|fiscal|government spending|避税|财政|税收|减税|加税|税务|政府支出'),
        ('货币政策', r'\bFed\b|FOMC|Federal Reserve|central bank|\bECB\b|美联储|央行|降息|加息'),
        ('AI 安全', r'(?:\bAI\b|OpenAI|Anthropic|人工智能|模型).*(?:safety|安全|失控|存在性风险|生存风险)'
         r'|(?:safety|安全).*(?:\bAI\b|OpenAI|Anthropic|人工智能|模型)'),
        ('选举与司法', r'election|electoral|选举|选务'),
        ('资本市场', r'\bIPO\b|listing|上市|招股|首次公开募股'),
        ('信用市场', r'subprime|credit risk|auto loans?|borrowers|次级|信贷|信用风险|汽车贷款|借款人'),
        ('债券市场', r'\bbonds?\b|债券|国债'),
        ('通胀数据', r'inflation|\bCPI\b|\bPCE\b|通胀|物价指数'),
        ('就业市场', r'payroll|unemployment|labor market|非农|失业|就业'),
        ('国际贸易', r'tariff|trade|关税|贸易'),
        ('资本流动', r'capital flows|fund flows|foreign capital|资金流|外资'),
        ('外汇市场', r'\bforex\b|exchange rate|currency market|外汇|汇率'),
        ('经济增长', r'\bGDP\b|\bPMI\b|economic growth|recession|经济增长|经济衰退'),
        ('地缘政治', r'ceasefire|terror|suspects|战争|停火|嫌疑人|恐怖'),
    ):
        if _has(pattern, text):
            return topic
    if _has(_CHINA, text):
        return '中国经济'
    # Unclassified is not evidence that unrelated facts share a topic. The
    # renderer keeps these independent without inventing specific headings.
    return '其他宏观'


def macro_topics(texts: list[str]) -> list[str]:
    """Resolve an edition together so elliptical follow-ups keep their theme."""
    topics = [macro_topic(text) for text in texts]
    if '中美关系' in topics:
        for i, text in enumerate(texts):
            if (topics[i] == '中国经济' and _has(_CHINA, text)
                    and _has(r'self.sufficien|自给自足', text)
                    and _has(r'negotiat|talks|bargain|谈判|磋商', text)
                    and not _has(r'European|Europe|Russia|Japan|欧盟|欧洲|俄罗斯|日本', text)):
                topics[i] = '中美关系'
    return topics
