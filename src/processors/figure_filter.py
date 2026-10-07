"""
关键人物发言筛选与摘要(模块 3a/3b 加工,ADR-0011 双层质量门槛 + 质量评分 + 版面限流)。

输入:list[FigureBundle](已经过 M3 第一道规则筛选 + 7 天 dedupe)
处理:
  1. 规则层预筛:候选包含明确引述或发言场合，无需逐字引号
  2. LLM 层判断:是否为本人近期发声 + 质量评分(1-5) + 跨媒体合并 + 提炼关键观点
  3. 事实等价兜底:只删除规范化后相同的事实,未知改写保留候选
  4. 版面限流(select_voice_summaries):最多 3 位人物,每人最多 1 条观点

输出:list[FigureSummary](人物 → 中文摘要 list)
- items 为空 → main.py 端整人物从渲染中剔除
- 全人物均空 → 调 generate_silence_note() 写一句古典韵味占位语
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime

from src.collectors.figures import FigureBundle, FigureMention
from src.processors.html_safe import is_safe_url
from src.processors.llm_client import LLMClient
from src.processors.news_presentation import PRESENTATION_VERSION, voice_text
from src.processors.news_selection import (
    chinese_prose,
    factual_excerpt,
    meaningful_quote,
    plain_source,
)
from src.processors.source_grounding import (
    checked_excerpt,
    diagnostic_text,
    grounded_text,
    publication_diagnostic,
)
from src.utils.dates import to_beijing
from src.utils.news_facts import content_key, equivalent
from src.utils.secrets import redact_secrets

logger = logging.getLogger(__name__)

_MAX_FILTER_ATTEMPTS = 2


# 直接引语标记词(中英混排,任一命中即视为可能含原话)
_QUOTE_MARKERS_RE = re.compile(
    r"[“”\"]\s*[^“”\"]{6,}\s*[“”\"]"  # 双引号包住的 6+ 字符
    r"|[「」]\s*[^「」]{6,}\s*[「」]"  # 中式引号
    r"|他说|她说|他表示|她表示|他认为|她认为|他指出|她指出"
    r"|表示称|声称|明确表示|公开表示|强调说"
    r"|\bsaid\b|\bsays\b|\btells?\b|told|stated|told reporters|in an interview|argued|claimed"
    r"|\b(?:warns?|warned|cautioned|noted|admitted|added|announces?|announced|expects?|predicts?|believes?|argues?)\b|表示|认为|指出|称|宣布"
    r"|发声|发表演讲|演讲中|采访中|公开信|致股东信",
    re.IGNORECASE,
)


def _has_quote_marker(item: FigureMention) -> bool:
    """规则层预筛:标题或摘要里含直接引语标记词才进入 LLM 层。"""
    title = plain_source(item.title or "")
    text = f"{title} {plain_source(item.snippet or '')}"
    # A company statement is not the configured person's speech. HTML href /
    # target attributes are not direct quotes either, even when RSS repeats it.
    from src.collectors.company_news import _RELEVANCE_KEYWORDS
    from src.collectors.figures import _FINNHUB_FALLBACK_ALIASES, FIGURES
    from src.collectors.frontier_labs import FRONTIER_LABS
    from src.config import HOLDINGS
    from src.processors.presentation_vocabulary import COMPANY_DISPLAY_NAMES
    from src.processors.speaker_attribution import _SPEECH

    companies = {h.name for h in HOLDINGS} | {lab.name for lab in FRONTIER_LABS} | set(COMPANY_DISPLAY_NAMES.values())
    companies.update(alias for aliases in _RELEVANCE_KEYWORDS.values() for alias in aliases)
    statement = re.match(r"^(.{1,80}?)\s+(?:says?|said|warns?|warned|announces?|announced|predicts?|expects?|believes?)\b", title, re.I)
    if statement:
        subject = statement[1].strip()
        names = {name for cn, _, _, en in FIGURES for name in (cn, en)}
        names.update(alias for aliases in _FINNHUB_FALLBACK_ALIASES.values() for alias in aliases)
        person_subject = (any(name.casefold() in subject.casefold() for name in names)
                          or re.search(r'\b(?:CEO|CFO|chairman|chairwoman|founder|president|minister)\b', subject, re.I))
        corporate_subject = (subject.casefold() in {name.casefold() for name in companies}
                             or re.search(r'\b(?:Inc|Corp|Corporation|Limited|Ltd)\.?$', subject, re.I))
        corporate_document = re.search(r'\bin (?:its |the |an? )?(?:own )?(?:IPO |annual |regulatory )?(?:filing|report|prospectus)\b', title, re.I)
        if not person_subject and (corporate_subject or corporate_document):
            return False
    if re.match(r"(?:" + '|'.join(re.escape(name) for name in companies) + r")\s*(?:表示|宣布|称|警告|预计)", title, re.I):
        return False
    return bool(_QUOTE_MARKERS_RE.search(text) or re.search(_SPEECH, text, re.I))


def candidate_window(bundle: FigureBundle, *, limit=5) -> list[FigureMention]:
    """Rank candidates, retaining alternative sources and every changed fact.

    Identity/source ranks only allocate work; they never prove attribution or
    freshness. Literal duplicate headlines share the first-pass slot, with
    alternatives retained for bounded recovery.
    """
    from urllib.parse import urlsplit

    from src.collectors.news_context import _HOSTS, _SOURCE_LABELS
    from src.processors.speaker_attribution import _named_speech, needs_speaker_context
    from src.utils.news_facts import canonical_fact

    eligible = [i for i in bundle.items if meaningful_quote(i) and _has_quote_marker(i)]
    def rank(item):
        raw = plain_source(item.title) + ' ' + plain_source(item.snippet)
        identity = (0 if _named_speech(raw, bundle.person, bundle.person_en)
                    else 1 if needs_speaker_context(item, bundle.person, bundle.person_en) else 2)
        supported = (urlsplit(item.url).hostname in _HOSTS or item.source.casefold() in _SOURCE_LABELS)
        return identity, not bool(getattr(item, 'source_published_at', '')), not supported
    ordered = sorted(eligible, key=rank)
    primary, alternatives, seen = [], [], set()
    for item in ordered:
        title = re.sub(r'\s*[-–—|]\s*' + re.escape(item.source) + r'\s*$', '', plain_source(item.title), flags=re.I) if item.source else item.title
        key = canonical_fact(title)
        (alternatives if key in seen else primary).append(item)
        seen.add(key)
    return (primary + alternatives)[:limit]


@dataclass
class FigureKeyPoint:
    """单条 LLM 提炼后的关键观点"""
    text: str  # 中文摘要,1-2 句
    source_url: str  # 原报道链接
    source_name: str  # 媒体名
    footnote_index: int = 0  # 全章节统一编号([1] [2] ...);0 表示未编号(异常)
    score: int = 0  # 具名且来源绑定通过的具体观点 >=3 可进入排序
    published_at: datetime | None = None
    evidence: list[dict] = field(default_factory=list)  # 原始报道时间,用于限流排序
    history_text: str = ""  # 保留署名前的核验文本，避免版式变动重置新闻去重
    date_note: str = ""


@dataclass
class FigureFootnote:
    """章节底部统一展示的脚注"""
    index: int
    url: str
    source: str


@dataclass
class FigureSummary:
    """单个人物的加工产物"""
    person: str
    person_en: str = ""  # 编辑式 byline 显示用,如 "Warren Buffett"。空则模板回退到 person
    items: list[FigureKeyPoint] = field(default_factory=list)
    fallback_raw: list[FigureMention] = field(default_factory=list)  # LLM 失败时模板用
    error: str | None = None
    processing_error: str | None = None
    content_rejections: list[str] = field(default_factory=list)
    verification_audit: list[dict] = field(default_factory=list)
    failure_kind: str = ""

    @property
    def translation_failure_count(self) -> int:
        return sum(reason.endswith("reason=translation_unavailable") for reason in self.content_rejections)


def _voice_date_note(item, bindings) -> str:
    """Prefer the observed publisher date to a newer RSS republication date."""
    observed = getattr(item, 'source_published_at', '') or item.published_at
    try:
        if isinstance(observed, str):
            observed = datetime.fromisoformat(observed.replace('Z', '+00:00'))
        note = f"报道日期 {to_beijing(observed):%m-%d}"
    except (TypeError, ValueError, AttributeError):
        return ''
    if any(binding.get('date_basis') == 'recent_reporting' for binding in bindings):
        note += '；发言日期未独立确认'
    return note


def assign_footnotes(summaries: list[FigureSummary]) -> list[FigureFootnote]:
    """跨人物给所有 items 分配 [1] [2] ... 全章节统一编号,返回 footnote 列表。
    主入口由 main.py 调,在 render_email 之前执行。"""
    footnotes: list[FigureFootnote] = []
    idx = 0
    for s in summaries:
        for kp in s.items:
            idx += 1
            kp.footnote_index = idx
            footnotes.append(FigureFootnote(
                index=idx,
                url=kp.source_url,
                source=kp.source_name,
            ))
    return footnotes


_TASK_INSTRUCTION = """\
任务:对下面"{PERSON}的候选发言列表"做三件事:
1. **人物与观点核验**:判断每条是否为媒体明确归属于**该{PERSON}本人的近期公开观点**
   (直接引语 / 演讲 / 采访 / 正式声明 / 公开信 / 媒体明确归属于本人的转述)。无需逐字引号；可靠报道中“某人表示/预计/宣布”也是发言。不得仅因没有逐字原话而淘汰，但人物身份、实际观点及近期性必须有来源支持。下列情况一律 no:
   - **历史发言追忆/旧闻回顾**(关键!):任何"X 年 X 月某场会议曾说""19 年股东大会
     表示""巴菲特 2019 年的判断""老黄当年讲过"——历史发言不是当前发声,一律 no。
     即使现在某媒体重新引用过去年份的旧话,也是 no。年份只是经营数据的比较基线时，
     不能据此判为旧发言。**只接受过去 7 天内本人
     新发声**；若原文明确署名、只是原发布日期未能补取，不要据此否决：
     程序会区分“发言日期已核实”和“近期媒体报道、发言日期未独立确认”。
     明确的旧发言仍淘汰。
   - **年终回顾/盘点文章**:"2024 年最经典的 5 句话""年度发言精选" — 一律 no
   - **公司官方说法/产品发布稿**冒充人物发言(如"NVIDIA 称 DLSS 5 是…",这是公司
     口径不是黄仁勋个人)
   - **媒体编辑标题党**:"老黄发声!""黄仁勋表态…"但内容只是产品评测/股价分析,
     没有真正的本人原话
   - **二手转述**:"分析师认为 X 同意"
   - **空洞口号**:"AI 是未来""市场需要谨慎""价值投资永不过时"等没有具体数字/
     具体事件/明确判断的话
   - **不相关内容**:讲的是公司业务/财报数字/股价波动,没引用人物本人的话
   - **不是{PERSON}的发言**(标题里出现别人的名字,主要内容是别人说的)
2. **跨媒体合并(重要)**:不同媒体(如第一财经、搜狐、Reuters、Bloomberg、CNBC)
   报道同一场演讲/采访/正式声明,即使措辞略有差异也必须**合并为一条**。
3. 对通过 1-2 的条目打分，并摘取对应的完整原文或已有译文。这里仅选稿；
   程序随后单独翻译并核验。原文语言、是否已有中文译文，不得影响 yes/no 或分数。
   英文原文同样可以选为 yes，不得因“无合格中文片段”淘汰。

【发言质量评分(1-5 分,必须打!)】
- **5 分**:重大判断,直接影响产业趋势、资本配置、长期投资假设或监管/竞争格局。
  例:明确 AI capex 方向、先进制程需求判断、并购意图、资本配置战略转向、监管立场改变。
- **4 分**:有明确方向、新信息、具体约束或可验证判断,值得日报展示。
  例:下季度指引、新产品路线图时间、具体产能数字、客户结构变化。
- **3 分**:真实且有具体对象或判断，信息普通也可以展示，由排序决定版面。
  例:针对特定产品、需求、竞争或经营约束的普通行业评论。
- **2 分**:真实但偏 PR、泛泛而谈、宣传意味强,不展示。
  例:"我们很兴奋""客户需求强劲""AI 是未来"等空泛口号。
- **1 分**:无效、旧闻、二手转述、标题党、人物花边、语录合集,不展示。

【高质量 vs 低质量发言定义】
高质量:
- 对未来经营、需求、供给、资本开支、技术路线、监管、竞争格局、并购、资本配置有明确判断
- 有具体对象、时间、数字、方向或约束条件
- 来自演讲、财报电话会、股东信、正式采访、监管文件、公司大会、官方声明
- 能让读者更新一个判断(如 AI capex、先进制程需求、云业务、广告、算力、模型商业化、
  安全监管、Berkshire 现金/回购/收购纪律等)

低质量:
- "AI 是未来""我们很兴奋""客户需求强劲"这类空泛口号
- 没有新信息的产品发布宣传
- 只是复述财报数字,没有人物本人判断
- 分析师或评论者猜测、推断人物观点（不包括明确归属于本人的新闻报道）
- 标题党:"某某重磅发声",但正文没有原话
- 老发言被重新包装
- 股价涨跌评论、花边、人物传记、排行榜、语录合集

输出严格按以下行格式,不要解释、不要前言:
▦ N: yes | score=5 | <完整原文或已有译文>             # 单条
▦ N,M[,K]: yes | score=5 | <完整原文或已有译文>       # 合并,M、K 与 N 是同一件事
▦ N: no  | score=2 | <淘汰原因>                  # 不合格,score 说明原因

【关键要求】
- 保留完整原句及其否定、数字、对象和条件，不改写、不补充；程序负责统一行文及人物署名。
- score 必须打数字 1-5;score 1-2 必须打 no(即使本人原话,内容不够格也不展示)

【跨媒体合并规则(重中之重!)】
下列仅为查找同源的线索，任何单一线索都不足以合并；须确认同一场合且观点事实等价，数字、对象、否定、状态变化必须分别保留：
- 提到的事件主体一致:同一公司财报、同一只股票、同一场会议名称
- 提到的具体数字接近:股价、估值、百分比基本相同
- 时间窗口相近:都是当周或近 3 天内的报道
- 摘要里描述的"是什么场合"一致:都说"在某次访谈中""在公开信中"

具体例子:
- 输入 1: 标题=但斌:看好 AI 前景,加仓英伟达 / 来源=第一财经
        2: 标题=但斌发声:AI 仍是未来主线 / 来源=搜狐
   → **必合并** 输出 "1,2: yes | score=4 | <提炼后观点>"
- 输入 1: 黄仁勋 GTC 演讲谈推理需求 / Reuters
        2: NVIDIA CEO at GTC: inference demand surging / Bloomberg
        3: 老黄:AI 工厂时代来临 / CNBC
   → **必合并** 输出 "1,2,3: yes | score=5 | <提炼>"
- 输入 1: 巴菲特谈苹果 / 2: 巴菲特谈中国市场
   → **保持独立**(不同观点不合并)

【硬约束】
    - 合并时主索引(N)取**信息最完整、来源最权威**的那条。来源优先级:
      官方一手源(OpenAI / Microsoft Blog / AMD IR / Berkshire Hathaway / 公司 IR 博客)
      > Reuters > Bloomberg > Financial Times / WSJ > CNBC > 其他财经媒体 > 门户聚合
    - 输出去重后每条观点必须**独立**,读者不应看到两条说同一件事
    - 每个输入索引必须出现在某行中**只一次**

【no 行要求】
- 一句话说明淘汰原因(他人转述 / 市场评论 / 二次解读 / 广告软文 / 空洞口号 / 列表帖)
- no 行不展示给读者,只供我们看 LLM 判断依据
"""


# 索引可以是单个(`1`)或逗号分隔合并(`1,3,5`),取第一个为代表来源
# score=N 正则可选,但业务上缺失 score 会丢弃
_LINE_RE = re.compile(
    r"^▦\s*([\d,\s]+?)\s*:\s*(yes|no)\s*"
    r"(?:\|\s*score\s*=\s*(\d)\s*)?"
    r"\|\s*(.+?)\s*$",
    re.IGNORECASE,
)


def _format_input(items: list[FigureMention]) -> str:
    lines: list[str] = []
    for i, it in enumerate(items, start=1):
        snippet = (it.snippet or "").strip()
        # 摘要太长会污染 prompt,裁到 200 字
        if len(snippet) > 200:
            snippet = snippet[:200].rstrip() + "…"
        excerpt, translated = checked_excerpt(it)
        line = (f"▦ {i}: 标题={it.title} / 完整原文={excerpt or factual_excerpt(it)}"
                f" / 译文参考={translated or '待翻译；请按原文选稿'}")
        if snippet:
            line += f" / 摘要={snippet}"
        line += f" / 来源={it.source}"
        line += f" / RSS日期={it.published_at}"
        if getattr(it, 'source_published_at', ''):
            line += f" / 原报道发布日期={it.source_published_at}（不可用RSS重发日期代替）"
        lines.append(line)
    return "\n".join(lines)


def _normalize(text: str) -> str:
    """归一化:去空白 + 去标点 + 小写,用于相似度算法。"""
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"[,。!?:;、—\-()()【】《》\"\"'']", "", text)
    return text.lower()


def _similar(a: str, b: str, threshold: float = 0.6) -> bool:
    return equivalent(a, b)


@dataclass
class _FigureParseResult:
    items: list[FigureKeyPoint]
    covered_indexes: set[int]
    duplicate_indexes: set[int]
    invalid_yes: bool = False
    rejected_indexes: dict[int, str] = field(default_factory=dict)
    decisions: list[dict] = field(default_factory=list)

    def is_complete(self, expected_count: int) -> bool:
        return (
            self.covered_indexes == set(range(1, expected_count + 1))
            and not self.duplicate_indexes
            and not self.invalid_yes
        )


def _parse_output_result(text: str, items: list[FigureMention], *, person='', person_en='') -> _FigureParseResult:
    kept: list[tuple[int, FigureKeyPoint]] = []
    index_counts: dict[int, int] = {}
    invalid_yes = False
    rejected_indexes = {}
    decisions = []
    for line in text.splitlines():
        m = _LINE_RE.match(line.strip())
        if not m:
            continue
        idx_part = m.group(1).strip()
        verdict = m.group(2).lower()
        score_str = (m.group(3) or "").strip()
        body = m.group(4).strip()
        valid_indexes: list[int] = []
        for tok in idx_part.split(","):
            try:
                value = int(tok.strip())
            except ValueError:
                continue
            if 1 <= value <= len(items):
                valid_indexes.append(value)
                index_counts[value] = index_counts.get(value, 0) + 1
        if not valid_indexes:
            if verdict == "yes":
                invalid_yes = True
            continue
        decisions.extend({"index": index, "verdict": verdict, "score": score_str,
                          "reason_or_claim": diagnostic_text(body, 600)} for index in valid_indexes)
        if verdict != "yes":
            continue
        # score 必须显式存在且为 1-5;缺失/格式错/越界一律丢弃
        try:
            score = int(score_str)
        except (ValueError, TypeError):
            logger.info("figure_filter.missing_score text=%s", body[:60])
            invalid_yes = True
            continue
        if score < 1 or score > 5:
            logger.info("figure_filter.invalid_score score=%d text=%s", score, body[:60])
            invalid_yes = True
            continue
        if score < (3 if person else 4):
            logger.info("figure_filter.low_score score=%d text=%s", score, body[:60])
            continue
        # A model merging indexes does not prove that their facts are equal.
        for source_index in valid_indexes:
            src_item = items[source_index - 1]
            if not is_safe_url(src_item.url):
                rejected_indexes[source_index] = "unsafe_source_url"
                continue
            if not meaningful_quote(src_item):
                continue
            supported, mapping = grounded_text(body, [src_item])
            if not supported:
                excerpt = factual_excerpt(src_item)
                rejected_indexes[source_index] = (
                    "source_evidence_unavailable" if not excerpt
                    else "translation_unavailable" if not chinese_prose(excerpt) and not checked_excerpt(src_item)[1]
                    else "publication_quality_rejected"
                )
                continue
            if person:
                from src.processors.speaker_attribution import attribution, source_date_error

                bindings = [attribution(src_item, person, person_en, excerpt=row['excerpt'], allow_recent_reporting=True) for row in mapping]
                if not all(bindings):
                    rejected_indexes[source_index] = source_date_error(src_item) or 'speaker_identity_unverified'
                    continue
                for row, binding in zip(mapping, bindings, strict=True):
                    row['speaker_attribution'] = binding
            kept.append((source_index, FigureKeyPoint(
                text=supported, evidence=mapping,
                source_url=src_item.url, source_name=src_item.source,
                score=score, published_at=src_item.published_at,
                date_note=_voice_date_note(src_item, bindings) if person else '',
            )))
    duplicates = {index for index, count in index_counts.items() if count > 1}
    unique = []
    seen_source_facts = set()
    for index, point in kept:
        key = content_key(items[index - 1])
        if index not in duplicates and key not in seen_source_facts:
            unique.append(point)
            seen_source_facts.add(key)
    return _FigureParseResult(
        items=unique,
        covered_indexes=set(index_counts),
        duplicate_indexes=duplicates,
        invalid_yes=invalid_yes,
        rejected_indexes=rejected_indexes,
        decisions=decisions,
    )


def _parse_output(text: str, items: list[FigureMention]) -> list[FigureKeyPoint]:
    """兼容既有调用的纯解析入口。"""
    return _parse_output_result(text, items).items


@dataclass
class _RecoveryBudget:
    # Shared by the whole voices section, not reset for every person. LLMClient
    # additionally enforces the production global token/time and wall cutoff.
    items: int = 6
    calls: int = 2
    selection_calls: int = 2


def _recover_selected(items, parsed, *, client, budget, audit):
    from src.processors.translator import translate_in_place_news

    retry = []
    for index in sorted(parsed.rejected_indexes):
        item = items[index - 1]
        excerpt = factual_excerpt(item)
        if (parsed.rejected_indexes[index] == "translation_unavailable" and excerpt
                and not chinese_prose(excerpt) and not checked_excerpt(item)[1]
                and len(retry) < budget.items):
            retry.append(item)
    if not retry or budget.calls <= 0:
        return False
    budget.items -= len(retry)
    record = {"phase": "translation_recovery", "before": [publication_diagnostic(i) for i in retry]}
    audit.append(record)
    pending = retry
    record["attempts"] = []
    while pending and budget.calls > 0:
        budget.calls -= 1
        attempt = {"before": [publication_diagnostic(i) for i in pending]}
        record["attempts"].append(attempt)
        try:
            translate_in_place_news(pending, client=client, max_attempts=1, timeout=20)
        except Exception as exc:
            attempt["error"] = diagnostic_text(f"{type(exc).__name__}: {exc}", 240)
        attempt["after"] = [publication_diagnostic(i) for i in pending]
        # Retry only unresolved selected evidence; successful siblings are immutable.
        pending = [i for i in pending if not checked_excerpt(i)[1]]
    record["after"] = [publication_diagnostic(i) for i in retry]
    record["recovered"] = sum(bool(checked_excerpt(i)[1]) for i in retry)
    logger.info("figure_filter.translation_recovery candidates=%d recovered=%d", len(retry), record["recovered"])
    return True


def filter_one(bundle: FigureBundle, *, client: LLMClient, max_items: int = 5, history=None, recovery_budget: _RecoveryBudget | None = None, _backfill=True) -> FigureSummary:
    """加工单个人物。
    流程:
      1. 规则层:候选必须含直接引语标记(双引号包句、说/表示、said/told 等)
      2. 全部不含 → 直接返回空(items=空 / fallback_raw=空 → main.py 整人剔除)
      3. LLM 层:质量门槛 + 跨媒体合并 + 提炼观点
      4. Python 端再做相似度兜底
    """
    if not bundle.items:
        return FigureSummary(person=bundle.person, person_en=bundle.person_en, error=bundle.error)
    feed_items = [item for item in bundle.items if meaningful_quote(item)]
    ranked = candidate_window(bundle, limit=max_items * 2)
    qualified = ranked[:max_items]
    logger.info(
        "figure_filter.rule_pass person=%s in=%d qualified=%d",
        bundle.person, len(feed_items), len(qualified),
    )
    preselection = {"phase": "rule_selection", "decisions": [
        {"url": item.url, "title": diagnostic_text(item.title, 400),
         "reason": ('selected' if item in qualified else 'low_information' if not meaningful_quote(item)
                    else 'no_attributed_speech_marker' if not _has_quote_marker(item) else 'candidate_limit')}
        for item in bundle.items][:120]}
    if not qualified:
        return FigureSummary(person=bundle.person, person_en=bundle.person_en, error=bundle.error,
                             verification_audit=[preselection])

    payload = _format_input(qualified)
    last_error: str | None = None
    kept = []
    rejected = []
    audit = []
    recovery_budget = recovery_budget or _RecoveryBudget()
    recovery_attempted = False

    def result(processing_error=None):
        nonlocal kept
        # The model ranks useful statements; it is not the sole admission gate.
        # If it rejects every item or fails, try already source-bound statements
        # at ordinary priority. Identity, dates and translations are replayed.
        if not kept:
            decisions = [decision for record in audit if record.get('phase') == 'selection'
                         for decision in record.get('decisions', [])]
            eligible = {d['index'] for d in decisions if str(d.get('score', '')).isdigit() and int(d['score']) >= 3}
            # Restore ordinary, concrete statements rejected only by ranking.
            # A score 1/2 factual/editorial rejection is not overridden by a
            # headline-name regex. Without model output, source verification
            # remains available for a bounded outage recovery.
            lines = '\n'.join(f"▦ {i}: yes | score=3 | {factual_excerpt(item)}"
                              for i, item in enumerate(qualified, 1) if not decisions or i in eligible)
            fallback = _parse_output_result(lines, qualified, person=bundle.person, person_en=bundle.person_en)
            if fallback.items:
                kept = fallback.items
                audit.append({'phase': 'verified_statement_recovery', 'recovered': len(kept)})
        for point in kept:
            point.history_text = '；'.join(dict.fromkeys(row.get('validated_text', row['output_text']) for row in point.evidence))
            for row in point.evidence:
                displayed = voice_text(row['output_text'], bundle.person, binding=row.get('speaker_attribution'))
                if displayed != row['output_text']:
                    row['presentation_operations'] = (*row.get('presentation_operations', ()), 'speaker_attribution')
                row['output_text'] = displayed
                row['presentation_version'] = PRESENTATION_VERSION
                row['presentation_speaker'] = bundle.person
            point.text = '；'.join(dict.fromkeys(row['output_text'] for row in point.evidence))
        errors = [redact_secrets(str(error))[:240] for error in [bundle.error, processing_error, *rejected] if error]
        failure_kind = ("processing" if processing_error else "source" if bundle.error
                        else "translation" if rejected and all(r.endswith("reason=translation_unavailable") for r in rejected)
                        else "evidence" if rejected else "")
        summary = FigureSummary(person=bundle.person, person_en=bundle.person_en, items=kept,
                                error="; ".join(errors) or None, processing_error=processing_error,
                                content_rejections=rejected, verification_audit=[*audit, preselection], failure_kind=failure_kind)
        return history.filter_figure(summary) if history is not None else summary

    for attempt in range(1, _MAX_FILTER_ATTEMPTS + 1):
        # The display nickname is not a separate person from the configured
        # canonical identity. Supply both to selection, then verify in code.
        identity = f"{bundle.person}（{bundle.person_en}）" if bundle.person_en else bundle.person
        instruction = _TASK_INSTRUCTION.format(PERSON=identity)
        if history is not None:
            instruction += history.context("figures", bundle.person)
        if attempt > 1:
            instruction += """

【重试修正】上一次输出为空、格式错误或遗漏了输入编号。这次每个输入编号必须
恰好出现一次；直接输出 ▦ 行，不要解释或 markdown。
"""
        try:
            resp = client.chat(payload, task_extra=instruction, max_tokens=2000,
                               temperature=0.1, timeout=30, thinking=False)
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {redact_secrets(str(exc))[:160]}"
            continue
        if not resp.text:
            last_error = redact_secrets(str(resp.error or "EmptyOutput"))[:240]
            logger.warning(
                "figure_filter.attempt_failed person=%s attempt=%d/%d reason=%s",
                bundle.person, attempt, _MAX_FILTER_ATTEMPTS, last_error,
            )
            continue

        parsed = _parse_output_result(resp.text, qualified, person=bundle.person, person_en=bundle.person_en)
        audit.append({"phase": "selection", "attempt": attempt, "decisions": parsed.decisions,
                      "rejected_indexes": dict(parsed.rejected_indexes),
                      "candidates": [{"index": index, **publication_diagnostic(item)}
                                     for index, item in enumerate(qualified, 1)]})
        if parsed.is_complete(len(qualified)) and parsed.rejected_indexes and not recovery_attempted:
            recovery_attempted = _recover_selected(qualified, parsed, client=client,
                                                   budget=recovery_budget, audit=audit)
            if recovery_attempted:
                # Revalidate the same selection; a new translation never changes
                # its score/identity or bypasses original source binding.
                parsed = _parse_output_result(resp.text, qualified, person=bundle.person, person_en=bundle.person_en)
        if parsed.covered_indexes:
            kept = parsed.items
            rejected = [f"index={index} reason={reason}" for index, reason in sorted(parsed.rejected_indexes.items())]
        if parsed.is_complete(len(qualified)):
            logger.info(
                "figure_filter.ok person=%s qualified=%d kept=%d attempt=%d",
                bundle.person, len(qualified), len(parsed.items), attempt,
            )
            completed = result()
            if not completed.items and _backfill and recovery_budget.selection_calls > 0 and len(ranked) > max_items:
                from src.collectors.news_context import enrich_speaker_context
                recovery_budget.selection_calls -= 1
                following = FigureBundle(bundle.person, bundle.query, bundle.person_en,
                                         ranked[max_items:], bundle.error)
                enrich_speaker_context([following], max_requests=2)
                next_result = filter_one(following, client=client, max_items=max_items,
                                         history=history, recovery_budget=recovery_budget, _backfill=False)
                previous = completed
                next_result.verification_audit = [*previous.verification_audit,
                    {'phase': 'candidate_backfill', 'candidates': len(following.items)}, *next_result.verification_audit]
                next_result.content_rejections = [*previous.content_rejections, *next_result.content_rejections]
                return next_result
            return completed

        last_error = (
            "IncompleteOrInvalidOutput: "
            f"covered={sorted(parsed.covered_indexes)}/{len(qualified)} "
            f"duplicates={sorted(parsed.duplicate_indexes)} invalid_yes={parsed.invalid_yes}"
        )
        logger.warning(
            "figure_filter.invalid_output person=%s attempt=%d/%d reason=%s",
            bundle.person, attempt, _MAX_FILTER_ATTEMPTS, last_error,
        )

    logger.warning(
        "figure_filter.failed person=%s attempts=%d reason=%s",
        bundle.person, _MAX_FILTER_ATTEMPTS, last_error or "unknown",
    )
    return result(last_error or "UnknownProcessingFailure")


def filter_all(
    bundles: list[FigureBundle], *, client: LLMClient, max_items: int = 5, history=None
) -> list[FigureSummary]:
    budget = _RecoveryBudget()
    return [filter_one(b, client=client, max_items=max_items, history=history, recovery_budget=budget) for b in bundles]


# ── 版面限流:质量评分后选择最优 3 位人物进入日报 ──

# 人物优先级(中英文双语 alias,避免 display name 变更导致命中失败):
# P0 > P1 > P2;同 P0(阿贝尔/巴菲特/黄仁勋) > P1(苏妈等) > P2(奥特曼/但斌等)
_FIGURE_PRIORITY: dict[str, int] = {
    # P0
    "巴菲特": 0, "Warren Buffett": 0,
    "阿贝尔": 0, "Greg Abel": 0,
    "黄仁勋": 0, "Jensen Huang": 0,
    # P1
    "苏妈": 1, "Lisa Su": 1,
    "魏哲家": 1, "C.C. Wei": 1,
    "Hock Tan": 1,
    "Christophe Fouquet": 1,
    "纳德拉": 1, "Satya Nadella": 1,
    "皮叉": 1, "Sundar Pichai": 1,
    # P2
    "奥特曼": 2, "Sam Altman": 2,
    "Dario Amodei": 2,
    "哈萨比斯": 2, "Demis Hassabis": 2,
    "但斌": 2, "Dan Bin": 2,
}

# 来源权威度:数字越小越权威,官方源 = 0,未知来源 = 5
_SOURCE_AUTHORITY: dict[str, int] = {
    # 官方一手源(最高权威)
    "OpenAI": 0, "Microsoft Blog": 0, "AMD IR": 0, "Berkshire Hathaway": 0,
    # 顶级通讯社/财经媒体
    "Reuters": 1, "Bloomberg": 2, "Financial Times": 3, "FT": 3,
    "Wall Street Journal": 4, "WSJ": 4, "CNBC": 5,
}


def select_voice_summaries(
    summaries: list[FigureSummary],
    *,
    max_figures: int = 3,
    max_items_per_figure: int = 1,
) -> list[FigureSummary]:
    """版面限流:在 LLM 评分后选出最优 N 位人物,每人最多 1 条观点。

    排序规则:
      1. score 高低(5 分优先于 4 分)
      2. 同分按人物优先级(P0 > P1 > P2)
      3. 同优先按来源权威度(Reuters > Bloomberg > FT/WSJ > CNBC > 其他)
      4. 同来源按发布时间新旧

    返回:最多 max_figures 位人物,每人最多 max_items_per_figure 条。
    无合格 items 的人物直接剔除。
    """
    # Step 1:每人保留最高分的一条(同分按来源权威度)
    for s in summaries:
        if len(s.items) <= 1:
            continue
        s.items.sort(key=lambda kp: (
            -kp.score,
            _SOURCE_AUTHORITY.get(kp.source_name, 5),
        ))
        s.items = s.items[:max_items_per_figure]

    # Step 2:按 score > 人物优先级 > 来源 > 时间排序
    def _sort_key(s: FigureSummary) -> tuple:
        if not s.items:
            return (-999, 999, 999, -9e18)
        kp = s.items[0]
        ts = kp.published_at.timestamp() if kp.published_at else 0
        return (
            -kp.score,
            _FIGURE_PRIORITY.get(s.person, 5),
            _SOURCE_AUTHORITY.get(kp.source_name, 5),
            -ts,  # 负号让 newer first
        )

    summaries.sort(key=_sort_key)

    # Step 3:截取 top-N
    result = [s for s in summaries[:max_figures] if s.items]
    limited = len(summaries) - len(result)
    if limited > 0:
        logger.info(
            "figure_filter.throttled limited=%d kept=%d",
            limited, len(result),
        )
    return result


_SILENCE_INSTRUCTION = """\
任务:今日所有关键人物都没有合格发言(无演讲、无采访、无正式声明)。
请写**一句**短小有古典韵味的中文(12-25 字),用作晨报「关键发言」章节的占位语,
让读者会心一笑或停顿一秒,而不是干巴巴的"今日无人发言"。

【风格基调】
- 化用古典意象、诗意句式,但不直接引用名句
- 节制、含蓄,有哲思而不说教
- 例如(只是范围参考,绝不要照抄):"群贤皆默,市自为声""智者三缄其口,世仍奔流不息"
  "今日大音希声"
- **不要**写"今日大佬未发言"这种平白叙述

【硬约束】
- 只输出**那句话本身**,不带前言、不带解释、不带 markdown、不带引号
- 字数 12-25 字,绝不超过 25 字
- 不要用"今天/今日"等明显时间副词
"""


def generate_silence_note(client: LLMClient) -> str | None:
    """全员未发言时生成的占位语。失败返回 None,模板用兜底文案。"""
    resp = client.chat(
        "请写一句替代'关键发言'章节的占位语",
        task_extra=_SILENCE_INSTRUCTION,
        max_tokens=256,
        temperature=0.85,
        thinking=False,
    )
    text = (resp.text or "").strip().strip("\"'“”「」 ")
    if not text:
        logger.warning("figure_silence.failed reason=%s", resp.error)
        return None
    if text.startswith("```"):
        text = text.strip("` \n")
    if len(text) > 50:
        text = text[:50].rstrip("。!?,;:") + "。"
    logger.info("figure_silence.ok chars=%d", len(text))
    return text
