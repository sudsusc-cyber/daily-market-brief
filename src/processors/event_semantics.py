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
EVENTS['raise'] += r'|\brising\b|加大|加剧|\badds? to (?=(?:the )?(?:pressure|costs?|risks?|burden)\b)'
EVENTS["cut"] += r"|\blayoffs?\b|走低"
EVENTS["launch"] += r"|\bintroduc(?:e|es|ed|ing)\b|\bgoes live\b|上线|出台"
EVENTS["payment"] += r"|开(?:出|具)(?:了)?(?:一张|季度|新的|另一张|又一张|一笔|新的季度|季度的|另一张季度)?支票|\bpayments?\b|\bpayable\b"
EVENTS["payment_completed"] = r"\b(?:has|have|had|already)\s+paid\b|已(?:经)?支付|已付"
EVENTS["payment_order"] = r"\b(?:orders?|ordered|requires?|required)\b.*\bpay\b|命令.*支付|责令.*支付|判令.*支付|判赔"
EVENTS["person_release"] = r"\bperson_release\b|释放"
EVENTS["pause"] = r"\b(?:paus\w*|suspend\w*|halt\w*)\b|暂停|中止"
EVENTS["delay"] = r"\b(?:delay\w*|postpon\w*|defer(?:s|red|ring)?)\b|推迟|延期|延后"
EVENTS["cancel"] = r"\b(?:cancel\w*|scrap\w*|abandon\w*|shelv\w*)\b|取消|放弃|搁置"
EVENTS["raise"] += r"|\b(?:scal(?:es|ed|ing)|powers?)\s+to\b|\b(?:intensif\w*|enhanc\w*)\b|\bfans?\s+(?:[\w-]+\s+){0,2}(?:fears?|concerns?|inflation)\b|扩展|提振|增强|加剧|劲升|激增|\b(?:sales|revenues?|profits?|earnings)\s+up\b|\bscal(?:e|es|ed|ing)\s+(?:energy|power|capacity|production|compute|computing|operations?|business)\b"
EVENTS['low_probability'] = r"\b(?:little|small|low) chance\b|可能性很小|概率很低|机会渺茫"
EVENTS['high_probability'] = r"\b(?:high|strong) chance\b|可能性很大|概率很高"
EVENTS["reserve_drawdown"] = r"\breserve_drawdown\b"
EVENTS["fall"] += r"|\b(?:slips?|dips?|dipped)\b|跌幅"
# Maintaining control/resilience is not a rate/price hold. Bind this polysemous
# verb to a financial state instead of requiring its Chinese word everywhere.
_HOLD_OBJECT = r"rates?|prices?|guidance|outlook|ratings?|dividends?|revenue|profit|production|利率|价格|指引|展望|评级|分红|营收|利润|产量"
EVENTS["hold"] = (r"\bunchanged\b|不变|持平(?=$|[，。；、！？,.;!?\s]|于|在|至|的|状态|水平)|\b(?:holds?|maintains?)\s+(?:\w+\s+){0,3}(?:" + _HOLD_OBJECT
                   + r")|(?:" + _HOLD_OBJECT + r").{0,12}(?:保持|维持)|(?:保持|维持).{0,8}(?:" + _HOLD_OBJECT + r")")
EVENTS['hold'] += r"|\bkeep\b.{0,45}\b(?:steady|unchanged)\b"
EVENTS['raise'] += r"|\b(?:commitments?|costs?|debts?|risks?|pressure|losses?)\s+(?:continue to\s+)?mount(?:s|ed|ing)?\b|加大"
EVENTS['pause'] += r"|停止"
EVENTS['asset_release'] = r"\basset_release\b"
EVENTS['noncancellable_commitment'] = r"\bnoncancellable_commitment\b"
MODALITY += r"|机会渺茫|概率很低|概率很高|\b(?:little|small|low|high|strong) chance\b|\bawait(?:s|ed)?\b|将(?=对|向|提供|给予|补贴|调整|进行)"

# Inflections belong to the event family; consumers must not maintain their own
# shorter synonym lists for the same action.
EVENTS['investigation'] = r"\b(?:investigat(?:e|es|ed|ing|ion|ions)|prob(?:e|es|ed|ing)|inquir(?:y|ies))\b|调查"
EVENTS['raise'] += r"|\braising\b|\bpops?\b|\bclimb(?:s|ed|ing)?\b|攀涨|爬升"
EVENTS['approval'] += r"|获准|核准"
EVENTS['launch'] += r'|\b(?:releasing|unveiling|debuting|introduction|roll(?:s|ed|ing)?[ -]out|switch(?:es|ed|ing)? on)\b'
EVENTS['cancel'] += r'|\baxes?\b|砍掉'
EVENTS['investment'] += r'|\bcapital expenditures?\b'
EVENTS['announcement'] = r"\bannounc(?:e|es|ed|ing|ement|ements)\b|宣布|公布"
EVENTS['fundraising'] = r'\bfundraising\b'


# Predicate families, shared across sources and all publication sections.
NEGATION += r"|(?:并)?不(?=畏惧|惧|担心|害怕|认同|支持|接受|认为)|并非"
MODALITY += r"|将(?=投入|花费|耗资|支出|培训|建设|扩建|增加|减少)"
EVENTS['raise'] += r"|新增(?=\s*(?:[0-9,]+\s*(?:个|名)?\s*)?(?:就业|岗位))|\blifting\b"
EVENTS['raise'] += r"|升至|升到"


def normalize_event_text(text: str) -> str:
    """Mask roles and disambiguate event objects without deleting quantities.

    Only used for semantic features; immutable source and published text retain
    every word. An investor is a role, not proof of a fresh capital investment.
    """
    # Raising money is financing, not a rise in prices/revenue. Preserve the
    # amount and prospective state; normalize the economic action on both sides.
    text = re.sub(r'\brais(?:e|es|ed|ing)\s+(?=(?:up to\s+)?(?:US\$|\$|€|\d|(?:(?:AI|new|fresh|additional|more)\s+){0,3}(?:money\b|funds?\b|capital\b)|financ\w*\b))', ' fundraising ', text, flags=re.I)
    text = re.sub(r'\b(?:fundrais(?:e|es|ed|ing)|funding|financing)\b|融资|筹资|募资|筹集(?:资金)?', ' fundraising ', text, flags=re.I)
    text = re.sub(r'\bSeries\s+[A-Z]\b', lambda m: m[0] + ' fundraising ', text)
    text = re.sub(r'(?<![A-Za-z])[A-Z]\s*轮(?!换|班)', lambda m: m[0] + ' fundraising ', text)
    # Releasing frozen property is neither a product launch nor a prisoner
    # release. Bind the object symmetrically before the event comparisons.
    text = re.sub(r"\b(?:the )?releas(?:e|es|ed|ing)\s+(?:of\s+)?(?:(?:the|its|their|frozen|blocked|seized|[A-Z][a-z]+)\s+){0,4}(?:assets?|funds?|collateral)\b",
                  ' asset_release ', text, flags=re.I)
    text = re.sub(r"释放(?:其|该国|被冻结的?|冻结的?|扣押的?|[一-鿿]{1,6})?(?:资产|资金|抵押品)", ' asset_release ', text)
    # A contractual restriction is not a cancellation event. Preserve the
    # restriction as its own feature, rather than attaching a plan elsewhere
    # to a phantom cancellation in the translation.
    text = re.sub(r"\bnon[ -]cancell?able\b|不可取消的?", ' noncancellable_commitment ', text, flags=re.I)
    # Directional predicates share semantics regardless of tense or word order.
    text = re.sub(r"\b(?:coming|comes?|came|going|goes|went) down\b", 'fall', text, flags=re.I)
    text = re.sub(r'\badded(?=\s+(?:just\s+)?[0-9,]+\s+jobs\b)', 'increased', text, flags=re.I)
    text = re.sub(r'开(?:出|具)(?:了)?(?:又)?(?:一张)?(?:季度)?支票', '支付支票', text)
    # Nominal classifications and abilities are not executed financial events.
    text = re.sub(r'\bgrowth fund\b|成长基金|增长型基金', 'fund_style', text, flags=re.I)
    text = re.sub(r'\bcan afford (?:its |the |a )?dividends?\b|(?:有能力|能够|能)(?:支付|负担)(?:其|该公司)?(?:的)?股息', 'dividend_affordability', text, flags=re.I)
    text = re.sub(r'送入(?=轨道)|送上(?=[^，。；]{1,25}火箭进入轨道)', '发射进入', text)
    # Raising children is not raising a financial quantity.
    text = re.sub(r"\brais(?:e|es|ed|ing) (?=children|kids|a child)\b", 'parenting ', text, flags=re.I)
    # Published statistical data are a report, not a product rollout.
    text = re.sub(r"\b(data|figures|statistics) (?:released|published)\b", r"\1 announced", text, flags=re.I)
    text = re.sub(r"发布(?=的?(?:数据|统计数字|统计结果)(?:显示|表明|[，。；]|$))", '公布', text)
    text = re.sub(r"((?:数据|统计数字|统计结果)(?:于|在)?[^，。；]{0,12})(?:发布)", r"\1公布", text)
    # An agreed action is a commitment, not an unqualified infinitive forecast.
    text = re.sub(r"\b(agreed|agrees) to (?=release\b)", r"\1 ", text, flags=re.I)
    # Bind polysemous words to their objects on both language sides.
    text = re.sub(r"\b(?:service|signal|connection|connectivity|data|packet|power)[ -]loss\b|(?:通信)?(?:服务|信号|连接|数据|电力)(?:中断|丢失)|断网", 'service_interruption', text, flags=re.I)
    reserve = r"(?:diesel|crude(?: oil)?|oil|fuel|petroleum|strategic|emergency|stocks?|reserves?|million|billion|barrels?|of|and|the|its|their|[0-9.,]+)"
    text = re.sub(r"\b(to\s+)?releas(?:e|es|ed|ing)\s+((?:" + reserve + r"[ -]+){0,12}(?:stocks?|reserves?|barrels?))\b",
                  lambda m: ('will ' if m[1] else '') + 'reserve_drawdown ' + m[2], text, flags=re.I)
    text = re.sub(r"释放(?=[^，。；,;]{0,35}(?:库存|储备|桶))", ' reserve_drawdown ', text)
    text = re.sub(r"将(?=\s*reserve_drawdown)", ' will ', text)
    # Writing a check is payment, not a reduction in the amount paid.
    text = re.sub(r"\bcut(?:s)? (?=(?:another |a |the )?(?:quarterly )?check\b)", 'paid ', text, flags=re.I)
    # Price adjectives and verbs express the same directional event.
    text = re.sub(r"\b(prices?|stocks?|shares?) lower\b", r"\1 fall", text, flags=re.I)
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
MODALITY += r"|将(?=继续|持续|借出|放贷|离开|离职)|旨在|(?<!同)意在|力求"

# Curly and straight apostrophes carry the same negative contraction.
NEGATION += r"|\b[A-Za-z]+n['’]t\b"
