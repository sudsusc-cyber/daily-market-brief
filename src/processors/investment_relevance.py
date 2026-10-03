"""Editorial noise classes, not an exhaustive whitelist of investable events.

Unknown events remain eligible for editorial selection and source verification.
A material operating fact can rescue an otherwise promotional headline. These
features select candidates; they never authorize a claim or investment advice.
"""
import re


def long_term_noise_reason(title: str, summary: str = '', *, holding_is_subject=True) -> str:
    context = title + ' ' + summary
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
