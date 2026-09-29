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
    # Public budgets are fiscal policy; corporate/project budgets are not.
    if (_has(r'\bbudgets?\b|预算', text) and
            _has(r'\b(?:EU|government|federal|national|state)\s+(?:\w+\s+){0,2}budgets?\b|European Union.{0,20}budget|(?:欧盟|政府|联邦|国家|财政).{0,8}预算', text)):
        return '财政政策'
    for topic, pattern in (
        ('科技监管', r'(?:AI|人工智能|科技|人才).*(?:travel|出行|出境|限制|禁令)|(?:travel curbs|出行限制|出境限制).*(?:AI|人工智能|人才)'),
        ('经济增长', r'pro.growth|促增长|增长政策'),
        ('国防开支', r'(?:missile|defen[cs]e|military|导弹|国防|军工).*(?:contract|spending|合同|开支)'
         r'|(?:contract|spending|合同|开支).*(?:missile|defen[cs]e|military|导弹|国防|军工)'),
        ('能源市场', r'\boil\b|\bcrude\b|\benergy\b|natural gas|油价|原油|石油|能源|天然气'),
        ('财政政策', r'\btax\w*\b|\bIRS\b|fiscal|government spending|避税|财政|税收|减税|加税|税务|政府支出'),
        ('货币政策', r'\bFed\b|FOMC|Federal Reserve|central bank|\bECB\b|(?:raises?|cuts?|hikes?|lowers?).{0,30}(?:benchmark|interest|policy) rates?|美联储|央行|降息|加息|(?:上调|下调).{0,8}(?:基准|政策|利率)|(?:基准|政策)利率.{0,6}(?:上调|下调)'),
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
    if _has(_CHINA, text) and _has(r'econom|经济', text):
        return '中国经济'
    # Unclassified is not evidence that unrelated facts share a topic. The
    # renderer keeps these independent with a neutral heading, without inventing connections.
    return '其他宏观'


def macro_topics(texts: list[str]) -> list[str]:
    """Resolve an edition together so elliptical follow-ups keep their theme."""
    topics = [macro_topic(text) for text in texts]
    if '中美关系' in topics:
        for i, text in enumerate(texts):
            if (topics[i] in {'中国经济', '其他宏观'} and _has(_CHINA, text)
                    and _has(r'self.sufficien|自给自足', text)
                    and _has(r'negotiat|talks|bargain|谈判|磋商', text)
                    and not _has(r'European|Europe|Russia|Japan|欧盟|欧洲|俄罗斯|日本', text)):
                topics[i] = '中美关系'
    return topics


# Editorial fallback priorities: economy-wide policy/data and systemic events
# outrank sector stories. Model order breaks ties, never publisher/feed order.
_MACRO_PRIORITY = {
    '货币政策': 100, '通胀数据': 95, '就业市场': 95, '经济增长': 95,
    '美债市场': 90, '中美关系': 90, '中东局势': 90, '俄乌局势': 90,
    '国际贸易': 85, '财政政策': 85, '信用市场': 80, '能源市场': 80,
    '债券市场': 75, '外汇市场': 75, '中国经济': 75, '资本流动': 70,
    '科技监管': 75, '地缘政治': 65, 'AI 安全': 60, '国防开支': 55,
    '选举与司法': 50, '资本市场': 45,
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
