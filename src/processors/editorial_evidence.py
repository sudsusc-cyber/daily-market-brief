"""Bounded publication checks beyond a valid link or a complete sentence.

These checks reject observable editorial/scope defects. They do not claim to
independently verify every report or replace human fact checking.
"""
import re
from urllib.parse import urlsplit


def editorial_issue(text: str) -> str | None:
    text = text.strip()
    if re.search(r"\b(?:beware|be wary of)\b.{0,60}\bhype\b|警惕.{0,40}炒作", text, re.I):
        return 'investment_opinion'
    if re.match(r"(?:NEWSLETTER|PODCAST|VIDEO)\s*[:：]", text, re.I) and re.search(r"Q&A|inside|a look|问答|解读", text, re.I):
        return 'reader_navigation'
    if re.match(r"(?:what|who|how|why|when|where)\s+(?:should|could|would|do|does|did|is|are|was|were|will|can|has|have)\b", text, re.I):
        return 'question_not_event'
    if re.search(r"\b(?:major|big|important)\s+(?:\w+\s+){0,2}change\s*(?:[-–—].*)?$", text, re.I):
        return 'unspecified_change'
    if re.search(r"\b(?:should be excited|what.s in store|stay tuned|read on to find out)\b", text, re.I):
        return 'reader_navigation'
    if re.search(r"\b(?:above|below)\s+(?:its\s+)?fair value\b|(?:高于|低于|高出|低出)公允价值|"
                 r"\b(?:overvalued|undervalued|overvaluation|undervaluation)\b|股价.{0,20}(?:高估|低估)|"
                 r"\b(?:trades?|trading)\s+at\s+[\d.]+\s+times\s+(?:earnings|sales)|"
                 r"\bis the (?:real|main|bigger) (?:story|takeaway)|才是(?:真正的)?看点", text, re.I):
        return 'valuation_or_editorial_opinion'
    if re.search(r"\b(?:holders|investors) are (?:counting on|hoping|betting)|"
                 r"(?:持有者|投资者)(?:指望|寄望)|\bhang over the stock\b|笼罩该股", text, re.I):
        return 'investment_opinion'
    # A headline about an undisclosed "part/thing/detail" contains no event
    # that can be checked against its source. A concrete reporting sentence in
    # the article body remains eligible through factual_excerpt().
    if (
        re.match(
            r"^(?:the|a|an)\s+(?:scary|surprising|shocking|overlooked|little[- ]known|hidden)\s+"
            r"(?:part|thing|detail|truth)\s+(?:about|in|behind|of)\b",
            text, re.I,
        )
        and not re.search(r"\b(?:is|are|was|were|shows?|reveals?|means?)\b", text, re.I)
    ) or re.search(r"(?:鲜为人知|少有人注意|令人震惊|可怕)的?(?:可怕的?)?(?:部分|细节|真相)[。.!?！？]?$", text):
        return 'teaser_without_fact'
    if re.search(r"\bsecret (?:weapon|ingredient|formula)\b|秘密武器|秘密配方", text, re.I) and not re.search(r"\b(?:launches|announced|reports|reported)\b|发布|宣布|公布", text, re.I):
        return 'teaser_without_fact'
    if re.search(r'[?？](?:[”\"\']|\s*[-—|].*)?$', text):
        return 'question_not_event'
    if re.match(r'(?:this|that|these|those|it|its)\b|此次|这次|上述|该(?:举措|诉讼)|该(?=.{0,12}(?:产品|组合))|这一(?:发布|举措|增长)|此举', text, re.I):
        return 'unresolved_context'
    if re.search(r"^(?:what we know|see how|here[’']s (?:what|the)|a look at)\b|"
                 r"[—–]\s*here[’']s\b|^(?:看看|以下是|关于.{1,80}的已知信息)", text, re.I):
        return 'reader_navigation'
    if re.search(r"\b(?:bold|stunning|shocking) predictions?\b|大胆预测|"
                 r"long.term fundamentals|长期基本面", text, re.I):
        return 'opinion_without_event'
    if re.search(r'analyst blog (?:highlights?|mentions?)|分析师博客.*(?:提及|关注)|博客重点提及', text, re.I):
        return 'media_roundup'
    if re.search(r'(?:for|over|than) (?:\d+|a hundred) years|已(?:有|超过).*年', text, re.I) and re.search(
            r'rated debt|评级债务|hundred|百年', text, re.I):
        return 'historical_background'
    if re.search(r'(?:makes? (?:a )?major push into|大举进军).*market|大举进军.*市场', text, re.I):
        return 'promotional_context_missing'
    if re.search(r'\b(?:finds?|sees?|identifies?|picks?)\b.{0,40}\b(?:winners?|winning stocks?)\b|'
                 r'(?:发现|看好|选出).{0,20}(?:赢家|受益股)', text, re.I):
        return 'investment_opinion'
    if re.search(r'provide\w*.*(?:revenue )?visibility|提供.*(?:收入|营收)可见性|'
                 r'positioned to benefit|evergreen investments|competitive assets?|'
                 r'highlights?.*effort|凸显.*努力', text, re.I):
        return 'investment_opinion'
    if re.search(r'\bhints? at\b.{0,45}\b(?:competitive|competition|dynamic|moat)\b|暗示.{0,25}(?:竞争格局|竞争态势|护城河)', text, re.I):
        return 'investment_opinion'
    return None


def analysis_source(title: str, summary: str = '') -> bool:
    return bool(re.search(
        r'\b(?:buy before|stock is a buy|investment case|analyst blog|here.s why.*moat|'
        r'political asset|wish you.*bought)\b|投资逻辑|值得买|分析师博客', title + ' ' + summary, re.I)
        or re.search(r'^(?:.{1,80}\s+)?(?:Analysis|Opinion|Commentary)\s*[:：]|^(?:.{1,40})?(?:分析|评论|观点)[:：]', title, re.I)
        or re.search(r'\bwhat\b.{0,100}\btell(?:s)? (?:investors|shareholders)\b|\bhere.s why\b.{0,100}\b(?:matters?|means?) (?:for|to) (?:investors|shareholders)\b', title, re.I))


# Event objects, not the issuer name, determine whether model-scope evidence is
# required. An AI company can also delay financing, a meeting or a building.
_STATUS_ACTION = re.compile(r"\bpull(?:s|ed|ing)?(?=\s+(?:[\w-]+\s+){0,8}back\b)|\bwithdraw(?:s|n|ing)?\b|\b(?:paus\w*|cancel\w*|scrap\w*|halt\w*|suspend\w*|abandon\w*|axes?|shelv\w*|postpon\w*|delay\w*|defer\w*)\b|暂停|取消|搁置|终止|砍掉|放弃|推迟|延后|延期|撤回", re.I)
_STATUS_OBJECTS = {
    "model": r"\b(?:models?|GPT[- .]?\d[\w.-]*|API|training|evaluation|inference|deployment|services?|subscriptions?)\b|模型|训练|评估|推理|部署|服务|订阅",
    "corporate": r"\b(?:IPOs?|financing|funding|fundrais\w*|offerings?|listings?|budgets?|meetings?|conferences?|construction|factories|factory|contracts?|acquisitions?|mergers?)\b|上市|融资|募资|预算|会议|大会|建设|工厂|合同|收购|并购",
}


def model_status_claim(text: str) -> bool:
    # Separate causal context: "delays its IPO because models are unsafe" is a
    # financing event, whereas "delays its model launch" is a product event.
    for clause in re.split(r"[，,；;。!?]|\b(?:after|before|because|amid|while|until|due to)\b|由于|因为|此前", text, flags=re.I):
        objects = sorted((m.start(), m.end(), kind) for kind, pattern in _STATUS_OBJECTS.items()
                         for m in re.finditer(pattern, clause, re.I))
        for action in _STATUS_ACTION.finditer(clause):
            before = [o for o in objects if o[1] <= action.start()]
            after = [o for o in objects if o[0] >= action.end()]
            prior = before[-1] if before else None
            following = after[0] if after else None
            # Passive headlines: "IPO was delayed" / "模型暂停". Do not bind
            # an object's background mention past the first actual object.
            passive = prior and re.fullmatch(r"\s*(?:(?:is|was|are|were|has|have|had|been|being|will|be|gets?|got)\s+)*", clause[prior[1]:action.start()], re.I)
            bound = prior if passive else following or prior
            if bound:
                if bound[2] == "model":
                    return True
                # Coordinated objects share the action: cancelling an IPO AND
                # a model still contains a model-status claim. A financing
                # object must not exempt a second affected product.
                next_action = _STATUS_ACTION.search(clause, action.end())
                stop = next_action.start() if next_action else len(clause)
                if any(o[2] == "model" and bound[1] <= o[0] < stop
                       and re.search(r"\b(?:and|as well as)\b|以及|及|与|和", clause[bound[1]:o[0]], re.I)
                       for o in objects):
                    return True
                continue
            # An explicitly mentioned model issuer with an unspecified paused
            # release remains ambiguous and must still satisfy the scope gate.
            if (re.search(r"OpenAI|Anthropic|人工智能|\bAI\b", clause, re.I)
                    and re.search(r"launch|release|rollout|发布|推出|上线", clause, re.I)):
                return True
    return False


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
