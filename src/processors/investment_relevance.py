"""Editorial noise classes, not an exhaustive whitelist of investable events.

Unknown events remain eligible for editorial selection and source verification.
A material operating fact can rescue an otherwise promotional headline. These
features select candidates; they never authorize a claim or investment advice.
"""
import re


def long_term_noise_reason(title: str, summary: str = '', *, holding_is_subject=True) -> str:
    context = title + ' ' + summary
    # Dividend commentary needs a disclosed change/amount, not simply the
    # assertion that a firm can pay or has written its routine quarterly check.
    dividend_commentary = re.search(
        r"\b(?:can afford (?:its |the )?dividend|(?:cut|wrote|written) another quarterly check)\b"
        r"|(?:有能力|能够|能)支付.{0,8}股息|又.{0,8}(?:季度支票|季度派息)", context, re.I)
    disclosed = any(
        re.search(r"dividend|payout|股息|分红|派息", part, re.I)
        and re.search(
            r"(?:\$|USD|HKD)\s*\d|\d[\d.,]*\s*(?:%|cents?|per share|美元|港元|每股)"
            r"|(?:raises?|raised|increases?|increased|cuts?|reduced|suspends?|suspended)\s+(?:its |the )?dividend"
            r"|(?:提高|削减|暂停|取消).{0,8}(?:股息|分红)", part, re.I)
        for part in re.split(r"(?<=[。!?])|(?<=\.)\s+(?=[A-Z])", context)
    )
    disclosed = disclosed or bool(re.search(
        r"\b(?:reported|reports|announced)\b.{0,45}\b(?:revenue|earnings|cash flow|profit)\b.{0,30}\d"
        r"|(?:公布|报告).{0,20}(?:营收|利润|现金流).{0,15}\d", context, re.I))
    if dividend_commentary and not disclosed:
        return 'undisclosed_dividend_commentary'
    impact = bool(re.search(
        r'\b(?:revenue|earnings|profit|margins?|cash flow|royalt\w*|licensing (?:income|revenue)|'
        r'paid subscribers?|market share|production capacity|retention|renewal rate)\b|'
        r'营收|收入|利润|毛利|现金流|授权收入|版税|付费用户|市占率|市场份额|产能|续费率|留存率|'
        r'\b(?:signs?|signed|renews?|renewed|wins?|won)\b.{0,60}\b(?:contracts?|licen[cs]ing agreements?)\b|'
        r'(?:签署|续签|获得).{0,30}(?:合同|授权协议)|'
        r'\b(?:recall|antitrust|regulatory approval|patent infringement|exclusive rights)\b|'
        r'召回|反垄断|监管批准|专利侵权|独家权利|'
        r'\b(?:enters?|entered|expands?|expanded)\b.{0,50}\b(?:markets?|distribution|channels?)\b|'
        r'(?:进入|拓展|扩大).{0,20}(?:市场|销售渠道|分销)|'
        r'(?:sales|销量|销售额).{0,35}(?:\d|grow|rise|fall|增长|下降)', context, re.I))
    noise = {
        'incidental_brand_appearance': r'(?:实拍|街拍|现场见闻|赛场内外|红毯).{0,200}(?:挂件|背包|玩偶|文创|设备|车辆|服饰|品牌|标识|亮相)|'
                                     r'\b(?:spotted|pictured|seen)\s+(?:wearing|carrying|using)\b|'
                                     r'\b(?:red carpet|street style|sidelines)\b.{0,100}\b(?:brand|logo|bag|outfit|merchandise)\b',
        'personal_trade_opinion': r'\b(?:keeps? me|makes? me|I (?:keep|am|will|would))\s+(?:buying|selling|holding)\b|(?:让我|我会|我仍|我持续).{0,6}(?:买入|卖出|持有)',
        'unquantified_outlook': r'\b(?:well.positioned|in a good position|should (?:increasingly )?worry|should be worried)\b|(?:应|应该).{0,6}(?:越来越)?担心|处于有利位置',
        'market_price_roundup': r'(?:指数|[沪深恒纳]指|恒科指|股市).{0,80}(?:上涨|下跌|下挫|跌幅|涨幅|新低|新高)|(?:均|齐)(?:涨|跌)\s*\d|股价.{0,30}(?:上涨|下跌|大涨|大跌)',
        'price_milestone': r'\b(?:stock|shares?|share price)\b.{0,65}\b(?:record|high|low)\b|股价.{0,30}(?:新高|新低|纪录)',
        'historical_retrospective': r'\b(?:look back|looking back|over the decades|years ago)\b|回顾.{0,30}(?:投资|押注)|数十年前',
        'empty_opinion': r'\b(?:era|future|story)\b.{0,40}\b(?:needs? more|more than|not enough)\b|时代.{0,25}(?:不仅|不只是|需要)',

        'personality_dispute': r'\b(?:privately confront|confronted|feud|war of words|trades? barbs|sparring)\b|口水战|隔空互怼|私下.{0,15}质问',
        'cosmetic_merchandise': r'联名款?礼盒|联名.{0,25}(?:礼盒|配色|包装)|限定包装|'
                               r'\b(?:co.branded|limited.edition).{0,35}(?:gift box|colorway|packaging)\b',
        'trading_flow': r'南向资金|北向资金|资金流入榜|资金流出榜|龙虎榜|'
                        r'\b(?:southbound|northbound)\b.{0,40}\b(?:buys?|adds?|shares?|flows?)\b',
        'consumer_instruction': r'^(?:how to|tips for|guide to using)\b|'
                                r'^(?:如何|教你|教程).{0,25}(?:设置|使用|安装|下载|领取)',
        'ceremonial_promotion': r'\b(?:partner of the year|inner circle award|wins? an? award|award.winning)\b|'
                                r'荣获.{0,35}(?:奖项|大奖|称号)|入选.{0,25}(?:雇主榜|品牌榜|人气榜)',
    }
    reason = next((reason for reason, pattern in noise.items() if re.search(pattern, title, re.I)), '')
    if (not reason and not holding_is_subject
            and re.search(r'\b(?:now )?integrated with\b|宣布.{0,35}与.{0,35}(?:集成|兼容)', title, re.I)):
        reason = 'incidental_vendor_integration'
    return reason if reason and not impact else ''


def ambiguous_money_claim(text: str) -> bool:
    """A headline metaphor supplies no auditable economic role for its amount."""
    return bool(re.search(r'[$€£]\s*\d|\d\s*(?:亿|万|million|billion|trillion)', text, re.I)
                and re.search(r'\b(?:on the table|up for grabs|at stake)\b|拿出|摆上桌|赌注', text, re.I)
                and not re.search(r'\b(?:committed|invested|paid|spending|contract|revenue|assets under management)\b|承诺投入|实际支付|合同金额|收入|管理资产', text, re.I))
