"""Shared bilingual event contract for selection, translation and watchpoints.

Patterns describe event families and grammatical roles, never individual stories.
These bounded features do not claim to prove arbitrary natural-language meaning.
"""
import re

# Match negative phrases before checking event states (e.g. not approved).
NEGATION = r"\b(?:not|never|no|without|denies?|denied|cannot|can't|won't|hasn't|isn't|didn't|unapproved)\b|尚未|并未|没有|未获|未被|未能|不曾|否认|无法|不能|不会|不予|不批准|不计划|未经|而非|并非|不是|未(?=上调|下调|提高|降低|增加|减少|批准|支付|完成|推出|发布|收购|暂停|取消|维持)"
MODALITY = r"\b(?:may(?!\s+\d)|might|could|would|plans?|planned|planning|proposes?|proposed|proposal|expects?|expected|aims?|seeks?|seeking|consider(?:s|ed|ing)?|reportedly|rumou?rs?|consensus|pending|awaiting|wait|waits|said to|will|shall|intends?|scheduled|looms?)\b|\bto\s+(?:pay|invest|investigate|acquire|launch|release|appoint|lend|leave|exit)\b|即将|将(?=上市|支付|于|在|会|要|发布|推出|收购|投资|任命|启动|发射|出任|担任|生效)|可能|或将|拟|计划|预计|预期|提议|考虑|据传|据称|传闻|寻求|等待|待定|待批|尚待"
EVENTS = {
    "approval": r"\b(?:approv\w*|authoriz(?:e|es|ed|ing|ation)|clearance|greenlight\w*)\b|批准|获批|监管放行",
    "completion": r"\b(?:completed?|finalized?|closed the deal)\b|完成|已交割|已落地",
    "cut": r"\b(?:cuts?(?!\s+(?:[A-Za-z.]+\s+){0,4}off\b)|cutting(?!\s+(?:[A-Za-z.]+\s+){0,4}off\b)|trims?|trimmed|lowers?|lowered|reduces?|reduced|reduction)\b|下调|削减|降息|减少|降低|减产|裁减|裁员",
    "cut_off": r"\bcut(?:s|ting)?\s+(?:[A-Za-z.]+\s+){0,4}off\b|切断|隔绝|孤立",
    "payment": r"\b(?:pay|pays|paid|paying)\b|支付|付给",
    "raise": r"\b(?:raises?|raised|lifts?|lifted|upgrades?|upgraded|hikes?|hiked|increases?|increased|boosts?|boosted|expands?|expanded|expansion|growth|grew|grow\w*)\b|上调|加息|增加|提高|扩大|扩张|增长|扩建",
    "hold": r"\b(?:holds?|unchanged|maintains?|maintained)\b|维持|不变|保持|持平",
    "fall": r"\b(?:falls?|fell|declines?(?!\s+to\b)|declined(?!\s+to\b)|drops?|dropped|slumps?|slumped)\b|下降|下跌|回落|下滑",
    "rise": r"\b(?:rises?|rose|gains?|gained|surges?|surged|rallies|rallied|is up)\b|上升|上涨|攀升|飙升",
    "profit": r"\b(?:profits?|earnings(?!\s+(?:release|call|date)\b)|net income)\b|盈利|利润|收益(?!率)",
    "loss": r"\b(?:loss|losses)\b|亏损",
    "revenue": r"\b(?:revenues?|sales)\b|营收|收入|销售",
    "investigation": r"\binvestigat(?:e|es|ed|ing|ion|ions)\b|调查",
    "refusal": r"\b(?:declin(?:e|es|ed|ing)|refus(?:e|es|ed|ing))\s+to\b|拒绝",
    "investment": r"\b(?:invest(?:s|ed|ing|ment|ments)?|capex|capital spending)\b|投资|资本开支|资本支出",
    "acquisition": r"\b(?:acqui\w*|merger|takeover|buyout)\b|收购|并购|合并",
    "launch": r"\b(?:launch\w*|rolls? out|rollout|switched on|starts?|releases?|released|unveils?|unveiled|debuts?|debuted)\b|发布|推出|亮相|发射|启用|启动|开通",
}
# Direction synonyms are one fact, not separate events that a translation must
# repeat twice ("revenue rose" and "revenue increased" both mean 营收增长).
EVENTS["raise"] += "|" + EVENTS.pop("rise") + r"|\bexpanding\b"
EVENTS["cut"] += r"|\blayoffs?\b|走低"
EVENTS["launch"] += r"|\bintroduc(?:e|es|ed|ing)\b|\bgoes live\b|上线|出台"
EVENTS["payment"] += r"|\bpayments?\b|\bpayable\b"
EVENTS["payment_completed"] = r"\b(?:has|have|had|already)\s+paid\b|已(?:经)?支付|已付"
EVENTS["payment_order"] = r"\b(?:orders?|ordered|requires?|required)\b.*\bpay\b|命令.*支付|责令.*支付|判令.*支付|判赔"
EVENTS["person_release"] = r"\bperson_release\b|释放"
EVENTS["pause"] = r"\b(?:paus\w*|suspend\w*|halt\w*)\b|暂停|中止"
EVENTS["delay"] = r"\b(?:delay\w*|postpon\w*|defer(?:s|red|ring)?)\b|推迟|延期|延后"
EVENTS["cancel"] = r"\b(?:cancel\w*|scrap\w*|abandon\w*|shelv\w*)\b|取消|放弃|搁置"
EVENTS["raise"] += r"|\b(?:scal(?:es|ed|ing)|powers?)\s+to\b|\b(?:intensif\w*|enhanc\w*)\b|\bfans?\s+(?:[\w-]+\s+){0,2}(?:fears?|concerns?|inflation)\b|扩展|提振|增强|加剧|劲升|激增|\b(?:sales|revenues?|profits?|earnings)\s+up\b|\bscal(?:e|es|ed|ing)\s+(?:energy|power|capacity|production|compute|computing|operations?|business)\b"
EVENTS["fall"] += r"|\bslips?\b|跌幅"
# Maintaining control/resilience is not a rate/price hold. Bind this polysemous
# verb to a financial state instead of requiring its Chinese word everywhere.
_HOLD_OBJECT = r"rates?|prices?|guidance|outlook|ratings?|dividends?|revenue|profit|production|利率|价格|指引|展望|评级|分红|营收|利润|产量"
EVENTS["hold"] = (r"\bunchanged\b|不变|持平(?=$|[，。；、！？,.;!?\s]|于|在|至|的|状态|水平)|\b(?:holds?|maintains?)\s+(?:\w+\s+){0,3}(?:" + _HOLD_OBJECT
                   + r")|(?:" + _HOLD_OBJECT + r").{0,12}(?:保持|维持)|(?:保持|维持).{0,8}(?:" + _HOLD_OBJECT + r")")
MODALITY += r"|\bawait(?:s|ed)?\b|将(?=对|向|提供|给予|补贴|调整|进行)"

# Inflections belong to the event family; consumers must not maintain their own
# shorter synonym lists for the same action.
EVENTS['investigation'] = r"\b(?:investigat(?:e|es|ed|ing|ion|ions)|prob(?:e|es|ed|ing)|inquir(?:y|ies))\b|调查"
EVENTS['raise'] += r"|\braising\b|\bpops?\b|\bclimb(?:s|ed|ing)?\b|攀涨|爬升"
EVENTS['approval'] += r"|获准|核准"
EVENTS['launch'] += r'|\b(?:releasing|unveiling|debuting|introduction|roll(?:s|ed|ing)?[ -]out|switch(?:es|ed|ing)? on)\b'
EVENTS['cancel'] += r'|\baxes?\b|砍掉'
EVENTS['investment'] += r'|\bcapital expenditures?\b'
EVENTS['announcement'] = r"\bannounc(?:e|es|ed|ing|ement|ements)\b|宣布|公布"


def normalize_event_text(text: str) -> str:
    """Mask roles and disambiguate event objects without deleting quantities.

    Only used for semantic features; immutable source and published text retain
    every word. An investor is a role, not proof of a fresh capital investment.
    """
    text = re.sub(r"\binvestor(?:s)?(?: relations)?\b|投资者关系|投资者|投资人", 'capital_provider', text, flags=re.I)
    text = re.sub(r"\binvestment (?:banks?|banking|firms?|managers?|management)\b|投资银行|投资公司|投资机构|投资管理", 'financial_institution', text, flags=re.I)
    # Participation in a financing round is an investment relation, unlike
    # participation in a conference or a product preview.
    def financing_participation(match):
        clause = match[0]
        if re.search(r"\b(?:round|financing|funding|Series [A-Z])\b", clause, re.I):
            return re.sub(r"\bparticipation (?:from|by)\b", 'investment from', clause, flags=re.I)
        return clause
    text = re.sub(r"[^.!?。]+", financing_participation, text)
    # The release/launch of an inquiry is not the release/launch of a product.
    modifiers = r"(?:an?|the|broad|new|formal|antitrust|regulatory|criminal|civil|independent|joint|sweeping|comprehensive|consumer[- ]protection|safety|security|privacy)"
    text = re.sub(r"\b(?:launch(?:es|ed|ing)?|start(?:s|ed|ing)?|open(?:s|ed|ing)?)\s+((?:" + modifiers + r"\s+){0,6}(?:investigation|probe|inquiry)\b)", r'opens \1', text, flags=re.I)
    text = re.sub(r"(?:启动|发起|开启)(?=(?:对[^，。；,;]{1,40}的)?[^，。；,;]{0,12}调查)", '展开', text)
    # Financial reporting conventions describe realised results, not forecasts.
    text = re.sub(r"\bearnings(?=\s+beats?\b)", 'results', text, flags=re.I)
    text = re.sub(r"(?:超出|超过|好于|超)(?:市场)?预期(?=财报|业绩|结果|$|[，。；,;])", '高于市场基准', text)
    # Reveal + a new product object is a launch; revealing a loss is not.
    text = re.sub(r"\breveal(?:s|ed)?(?=\s+(?:a|an|its)\s+new\s+(?:[\w-]+\s+){0,7}(?:platform|model|agent|app|service|chip|device|tool)\b)", 'unveils', text, flags=re.I)
    return text


def event_pattern(*names: str) -> str:
    return '|'.join('(?:' + EVENTS[name] + ')' for name in names)


def has_event(text: str, *names: str) -> bool:
    return bool(re.search(event_pattern(*names), normalize_event_text(text), re.I))


DOCUMENT_TYPES = {
    'opinions_summary': r"\bsummary of opinions\b|意见摘要|意见概要",
    'meeting_minutes': r"\b(?:meeting |MPM |FOMC )?minutes\b|会议纪要|会议记录",
}


def document_types(text: str) -> set[str]:
    return {name for name, pattern in DOCUMENT_TYPES.items() if re.search(pattern, text, re.I)}

# Future continuations and lending headlines keep their prospective status.
MODALITY += r"|将(?=继续|持续|借出|放贷|离开|离职)"

# Curly and straight apostrophes carry the same negative contraction.
NEGATION += r"|\b[A-Za-z]+n['’]t\b"
