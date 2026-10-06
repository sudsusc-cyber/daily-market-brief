"""Source-bound event features used only for arranging verified facts.

This is a conservative bilingual grammar, not semantic proof. Recognizers name
objects/actions; decisions use clause roles and object-action relationships.
Unknown events remain independent. No generated prose is authorized here.
"""

import re
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Span:
    kind: str
    value: str
    text: str
    start: int
    end: int


@dataclass(frozen=True)
class MacroEvent:
    text: str
    topic: str
    family: str
    actor: str
    action: str
    object: str
    geography: tuple[str, ...]
    time: tuple[str, ...]
    states: tuple[str, ...]
    spans: tuple[Span, ...]
    decision: str

    def audit(self):
        return {"version": 1, **asdict(self)}


# Vocabulary describes domains, not particular articles or issuers. Matching a
# bank name alone deliberately creates no policy event.
OBJECTS = {
    "us_bonds": r"Treasuries|Treasury (?:yields?|bonds?|debt|selloff)|U\.?S\.? (?:government )?(?:bonds?|yields?|debt)|美债|美国国债",
    "policy_rates": r"(?:benchmark|interest|policy) rates?|基准利率|政策利率|降息|加息",
    "inflation": r"inflation|\bCPI\b|\bPCE\b|通胀|物价指数",
    "employment": r"payrolls?|\b(?:un)?employment\b|labor market|\bjobs?\b|非农|失业|就业",
    "growth": r"\bGDP\b|\bPMI\b|economic growth|recession|pro.growth|经济增长|经济衰退|增长政策|促增长",
    "budget": r"\bbudgets?\b|预算",
    "subsidy": r"subsid(?:y|ies|ize)|mortgage support|补贴|房贷支持",
    "tax": r"\btax(?:es|ation)?\b|\bIRS\b|fiscal|government spending|避税|财政|税收|减税|加税|税务|政府支出",
    "precious_metals": r"\bgold\b|\bsilver\b|bullion|黄金|白银|贵金属",
    "energy": r"\boil\b|\bcrude\b|\benergy\b|natural gas|油价|原油|石油|能源|天然气",
    "defense": r"missile|defen[cs]e|military|导弹|国防|军工",
    "trade": r"\b(?:tariffs?|trad(?:e|es|ing))\b|关税|贸易",
    "credit": r"subprime|credit risk|auto loans?|borrowers|次级|信贷|信用风险|汽车贷款|借款人",
    "bonds": r"\bbonds?\b|债券|国债",
    "fx": r"\b(?:dollar|yuan|yen|euro|sterling)\b(?=\s+(?:\w+\s+){0,4}(?:best|worst|strongest|weakest)\s+(?:day|week|month|quarter|year)\b)|"
          r"\bforex\b|exchange rate|currency market|外汇|汇率|"
          r"\b(?:dollar|yuan|yen|euro|sterling)\b(?=\s+(?:index|powers?|gains?|rises?|falls?|drops?|surges?|weakens?|strengthens?|slides?|slips?|plunges?|slumps?))|"
          r"(?:美元|欧元|人民币|日元|英镑)(?=指数|汇率|走强|走弱|上涨|下跌|飙升|劲升)|"
          r"\bpressure on (?:the )?(?:dollar|yuan|yen|euro|sterling|single currency)\b|对(?:美元|欧元|人民币|日元|英镑)(?:的)?压力",
    "flows": r"capital flows|fund flows|foreign capital|资金流|外资",
    "equity": r"\bIPO\b|listing|上市|招股|首次公开募股",
    "election": r"election|electoral|选举|选务",
    "security": r"ceasefire|terror|suspects|战争|停火|嫌疑人|恐怖",
    "ai": r"\bAI\b|OpenAI|Anthropic|人工智能|模型",
    "technology": r"technology|talent|科技|人才",
    "safety": r"safety|安全|失控|存在性风险|生存风险",
    "travel": r"travel|出行|出境",
}
ACTIONS = {
    "policy_change": r"\b(?:cuts?|raises?|hikes?|lowers?|holds?|maintains?)\b|降息|加息|上调|下调|维持",
    "release": r"releas\w*|publish\w*|report\w*|数据|指标|公布|发布",
    "restriction": r"\b(?:curbs?|restrict\w*|bans?|banned|banning)\b|限制|禁令",
    "spending": r"contract|spending|合同|开支",
    "negotiation": r"negotia\w*|discuss\w*|talks|summit|bargain|谈判|磋商|峰会|讨论|施压",
    "market_move": r"\b(?:rises?|falls?|rebounds?|gains?|drops?|surges?|yields?|selloff|steady|stable|suffer(?:s|ed)?)\b|上涨|下跌|上行|下行|反弹|承压|回升|走低|持稳|遭遇",
    "decision": r"announc\w*|approv\w*|reject\w*|宣布|批准|拒绝|决定",
}
ACTORS = {
    "central_bank": r"\bFed\b|FOMC|Federal Reserve|central bank|\bECB\b|美联储|央行",
    "public_authority": r"\b(?:EU|government|federal|national|state|parliament)\b|European Union|欧盟|政府|联邦|国家|议会|财政",
    "corporate": r"\b(?:company|corporation|factory|firm)\b|公司|工厂|企业",
}
REGIONS = {
    "china": r"China|Chinese|Xi Jinping|中国|中方|对华|习近平",
    "us": r"\bUS\b(?!\$|\s*dollars?)|U\.S\.(?!\s*dollars?)|United States|America\w*|Trump|美国|美方|特朗普",
    "middle_east": r"Iran|Saudi|Israel|Gaza|Hormuz|Middle East|Persian Gulf|\bU\.?A\.?E\.?\b|United Arab Emirates|\b(?:Qatar|Bahrain|Kuwait|Oman|Jordan|Lebanon|Syria|Yemen|Iraq)\b|伊朗|沙特|以色列|加沙|霍尔木兹|中东|美伊|红海|波斯湾|阿联酋|卡塔尔|巴林|科威特|阿曼|约旦|黎巴嫩|叙利亚|也门|伊拉克",
    "russia_ukraine": r"Russia|Ukraine|俄乌|俄罗斯|乌克兰",
    "other_gulf": r"Gulf of (?:Mexico|America|Finland|Guinea|Thailand)|墨西哥湾|美国湾|芬兰湾|几内亚湾|泰国湾",
    "other_partner": r"European|Europe|Japan|欧盟|欧洲|日本",
}
# Keep jurisdictions distinct when grouping a release with market reactions.
REGIONS.update({
    'canada': r"\bCanada|Canadian\b|加拿大", 'australia': r"\bAustralia|Australian\b|澳大利亚|澳洲",
    'uk': r"\bUK\b|United Kingdom|Britain|British|英国", 'india': r"\bIndia|Indian\b|印度",
    'euro_area': r"Eurozone|euro area|欧元区",
})
TOPICS = {
    "us_bonds": "美债市场",
    "policy_rates": "货币政策",
    "inflation": "通胀数据",
    "employment": "就业市场",
    "growth": "经济增长",
    "budget": "财政政策",
    "tax": "财政政策",
    "subsidy": "财政政策",
    "energy": "能源市场",
    "precious_metals": "贵金属市场",
    "defense": "国防开支",
    "trade": "国际贸易",
    "credit": "信用市场",
    "bonds": "债券市场",
    "fx": "外汇市场",
    "flows": "资本流动",
    "equity": "资本市场",
    "election": "选举与司法",
    "security": "地缘政治",
    "ai_safety": "AI 安全",
    "tech_restriction": "科技监管",
}


def _spans(kind, vocabulary, text):
    return [
        Span(kind, value, m[0], m.start(), m.end())
        for value, pattern in vocabulary.items()
        for m in re.finditer(pattern, text, re.I)
    ]


# Boundaries describe syntax rather than any particular financial story.
_BACKGROUND = re.compile(
    r"\b(?:because|after|amid|as(?!\s+(?:of|well|much|many|a\b|an\b))|while)\b|此前|由于|因为|随着|因(?!此|而|为|素|子|果|由)",
    re.I,
)


def _focus(text):
    match = _BACKGROUND.search(text)
    if not match:
        comma = re.search(r'[,，]', text)
        if comma:
            lead = text[:comma.start()]
            if (any(re.search(pattern, lead, re.I) for pattern in OBJECTS.values())
                    and re.search(ACTIONS['market_move'], lead, re.I)):
                return 0, comma.start()
        return 0, len(text)
    if not text[: match.start()].strip():
        # A leading subordinate clause ends at its comma: "After ..., bonds ...".
        comma = re.search(r"[,，]", text[match.end() :])
        return (match.end() + comma.end(), len(text)) if comma else (0, len(text))
    return 0, match.start()


def _context_bounds(text, start, end, focus):
    boundaries = [0, len(text)]
    boundaries.extend(m.end() for m in re.finditer(r"[。；;!?！？]", text))
    # English full stops, excluding abbreviations such as U.S. and Inc.
    for match in re.finditer(r"\.(?=\s|$)", text):
        prefix = text[: match.start()]
        word = re.search(r"[A-Za-z.]+$", prefix)
        token = word[0] if word else ""
        if token and (
            len(token) == 1
            or "." in token
            or token.casefold() in {"inc", "corp", "co", "ltd", "dr", "mr", "ms"}
        ):
            continue
        boundaries.append(match.end())
    left = max(p for p in boundaries if p <= start)
    right = min(p for p in boundaries if p >= end)
    if focus[0] <= start < focus[1]:
        left, right = max(left, focus[0]), min(right, focus[1])
    else:
        # A background actor cannot authorize a rate/budget event in the main clause.
        if end <= focus[0]:
            right = min(right, focus[0])
        elif start >= focus[1]:
            left = max(left, focus[1])
    return left, right


def extract_event(text: str) -> MacroEvent:
    spans = [
        *_spans("object", OBJECTS, text),
        *_spans("action", ACTIONS, text),
        *_spans("actor", ACTORS, text),
        *_spans("region", REGIONS, text),
        *_spans(
            "time",
            {
                "date": r"\b(?:19|20)\d{2}-\d{2}-\d{2}\b|(?<!\d)(?:19|20)\d{2}年|\d{1,2}月\d{1,2}日|周[一二三四五六日]|today|yesterday|tomorrow|Monday|Tuesday|Wednesday|Thursday|Friday"
            },
            text,
        ),
        *_spans(
            "state",
            {
                "negative": r"\bnot\b|denies?|尚未|没有|否认|不会",
                "planned": r"\b(?:may|could|plans?|expects?|will)\b|可能|计划|预计|将于|有望",
            },
            text,
        ),
    ]
    objs = [s for s in spans if s.kind == "object"]
    actions = [s for s in spans if s.kind == "action"]
    actors = [s for s in spans if s.kind == "actor"]
    regions = {s.value for s in spans if s.kind == "region"}
    focus = _focus(text)
    candidates = []
    # Object-specificity and same-clause action bindings replace first-hit wins.
    for obj in objs:
        clause_start, clause_end = _context_bounds(text, obj.start, obj.end, focus)
        local_actions = [s for s in actions if clause_start <= s.start < clause_end]
        local_actors = [s for s in actors if clause_start <= s.start < clause_end]
        family = obj.value
        score = 20 + (50 if focus[0] <= obj.start < focus[1] else 0)
        binding = min(local_actions, key=lambda s: abs(s.start - obj.start), default=None)
        local_objects = {s.value for s in objs if clause_start <= s.start < clause_end}
        if family == "budget" and not any(s.value == "public_authority" for s in local_actors):
            continue
        if family == "defense" and not any(s.value == "spending" for s in local_actions):
            continue
        if family == "policy_rates" and not (
            any(s.value == "central_bank" for s in local_actors)
            or re.search(r"benchmark|policy|基准|政策|降息|加息", obj.text, re.I)
        ):
            continue
        if family == "policy_rates":
            score += 25 if any(s.value == "policy_change" for s in local_actions) else 0
        if family == "inflation":
            score += 20 if any(s.value == "release" for s in local_actions) else 0
        if family == "us_bonds":
            score += 35
        if family == 'fx' and re.match(r'pressure on|对', obj.text, re.I):
            # The currency is the affected object; energy prices or public
            # finances in the same clause are causes, not the editorial topic.
            score += 25
        if family == "credit":
            score += 10
        if family == "safety" and "ai" in local_objects:
            family, score = "ai_safety", score + 25
        elif family in {"technology", "ai", "travel"}:
            if (
                "travel" in local_objects
                and local_objects & {"ai", "technology"}
                and any(s.value == "restriction" for s in local_actions)
            ):
                family, score = "tech_restriction", score + 30
            else:
                continue
        if family not in TOPICS:
            continue
        candidates.append((score, -obj.start, family, obj, binding))
    # Bare "rates" only denotes policy when an authority and rate decision bind.
    for rates in re.finditer(r"\brates?\b|利率", text, re.I):
        start, end = _context_bounds(text, rates.start(), rates.end(), focus)
        authorities = [s for s in actors if s.value == "central_bank" and start <= s.start < end]
        changes = [
            s
            for s in actions
            if s.value in {"policy_change", "decision"} and start <= s.start < end
        ]
        if authorities and changes:
            obj = Span("object", "policy_rates", rates[0], rates.start(), rates.end())
            spans.append(obj)
            candidates.append(
                (
                    95 if focus[0] <= rates.start() < focus[1] else 45,
                    -rates.start(),
                    "policy_rates",
                    obj,
                    min(changes, key=lambda s: abs(s.start - rates.start())),
                )
            )
    chosen = max(candidates, key=lambda c: (c[0], c[1]), default=None)
    family, obj, action = (chosen[2], chosen[3], chosen[4]) if chosen else ("unknown", None, None)
    topic = TOPICS.get(family, "其他宏观")
    decision = "object_action_in_focus" if chosen else "unresolved"
    main = text[focus[0] : focus[1]]
    main_regions = {s.value for s in spans if s.kind == "region" and focus[0] <= s.start < focus[1]}
    if family != "us_bonds" and (
        re.search(r"中美|美中|Sino[ -]American|U\.?S\.?[ -]China|China[ -]U\.?S\.?", main, re.I)
        or (
            {"china", "us"} <= main_regions
            and any(s.value == "negotiation" and focus[0] <= s.start < focus[1] for s in actions)
        )
    ):
        topic, decision = "中美关系", "bilateral_interaction"
    elif (
        family in {"unknown", "security", "energy", "trade", "defense", "equity"}
        and "middle_east" in regions
    ):
        topic, decision = "中东局势", "regional_context"
    elif family == "energy" and "other_gulf" not in regions and re.search(r"\bGulf\b|海湾", text):
        topic, decision = "中东局势", "regional_energy_context"
    elif "russia_ukraine" in regions and family in {
        "unknown",
        "security",
        "energy",
        "trade",
        "defense",
    }:
        topic, decision = "俄乌局势", "regional_context"
    elif topic == "其他宏观" and "china" in regions and re.search(r"econom|经济", text, re.I):
        topic = "中国经济"
    related_actors = []
    if obj:
        start, end = _context_bounds(text, obj.start, obj.end, focus)
        related_actors = [s for s in actors if start <= s.start < end]
    return MacroEvent(
        text,
        topic,
        family,
        related_actors[0].text if related_actors else "",
        action.text if action else "",
        obj.text if obj else "",
        tuple(sorted(regions)),
        tuple(s.text for s in spans if s.kind == "time"),
        tuple(sorted({s.value for s in spans if s.kind == "state"})),
        tuple(spans),
        decision,
    )


def _is_data_release(text):
    """A topic mention or interview heading cannot identify a released dataset."""
    from decimal import Decimal

    from src.processors.translation_guard import _quantities

    measured = any(isinstance(key[0], Decimal) and not (key[1] == '' and 1900 <= key[0] <= 2100)
                   for key in _quantities(text))
    return bool(measured and re.search(
        r'\b(?:adds?|added|increases?|increased|decreases?|decreased|rises?|rose|falls?|fell|grew|contracted|reports?|reported|released|showed|recorded)\b|'
        r'新增|增加|减少|升至|降至|增长|下降|公布|录得', text, re.I))


def _release_context(text, family):
    """Only dates attached to the cited release identify it, not next policy dates."""
    clauses = re.split(r'[,，;；]|\b(?:after|following|despite)\b', text, flags=re.I)
    return ' '.join(c for c in clauses if re.search(OBJECTS[family], c, re.I))


def edition_events(texts: list[str]) -> list[MacroEvent]:
    from dataclasses import replace

    events = [extract_event(text) for text in texts]
    # Group a data release with market reactions explicitly citing that release.
    # Mentioning an economy/market alone never joins unrelated stories.
    from src.processors.translation_guard import _calendar_months

    def months(text):
        return {match[0][:3].casefold() for match in _calendar_months(text)}

    for family in ('employment', 'inflation', 'growth'):
        anchors = [e for e in events if e.family == family and _is_data_release(e.text)]
        # Duplicate reporting on one release must not make its identity ambiguous.
        anchor_keys = {(tuple(e.geography), tuple(sorted(months(e.text)))) for e in anchors}

        for i, event in enumerate(events):
            if event.family not in {'us_bonds', 'bonds', 'fx', 'precious_metals', 'policy_rates'}:
                continue
            if not any(s.kind == 'object' and s.value == family for s in event.spans):
                continue
            if not re.search(r'\b(?:after|as|despite|following)\b|因|尽管|数据|报告', event.text, re.I):
                continue
            if (len(anchor_keys) == 1 and anchors and not (set(event.geography) - set(anchors[0].geography))
                    and not (months(_release_context(event.text, family)) and months(anchors[0].text)
                             and months(_release_context(event.text, family)) != months(anchors[0].text))):
                events[i] = replace(event, topic=TOPICS[family], decision='edition_data_release_reaction')
    if any(e.topic == "中美关系" for e in events):
        for i, event in enumerate(events):
            if (
                event.topic in {"中国经济", "其他宏观"}
                and "china" in event.geography
                and "other_partner" not in event.geography
                # Only elliptical negotiating-position follow-ups borrow the
                # edition context. An explicit counterpart (even an unknown
                # country) or a corporate negotiation must remain independent.
                and not re.search(
                    r"(?:China|中国|中方)\s*(?:and\b|with\b|与|和|同)|(?:with\s+|and\s+|与|和)China\b|与中国",
                    event.text,
                    re.I,
                )
                and not any(s.kind == "actor" and s.value == "corporate" for s in event.spans)
                and re.search(
                    r"谈判(?:盘算|策略|空间|地位)|(?:negotiating|bargaining) (?:position|strategy|leverage|calculus)",
                    event.text,
                    re.I,
                )
                and any(s.value == "negotiation" for s in event.spans)
            ):
                events[i] = replace(event, topic="中美关系", decision="edition_bilateral_followup")
    return events
