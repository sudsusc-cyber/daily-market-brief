"""
情绪综合判断(模块 5 加工)。

verdict(温度档位)由**确定性加权打分**得出,同样输入永远同样档位:
  - 5 指标各打 0-100 分(0=极度恐慌,100=极度贪婪)
  - 核心-辅助加权(CNN + VIX 决定主方向,其余指标只小幅校正)
  - 失败指标从权重中剔除并重新归一化;无核心指标时不生成结论
  - 阈值切档:<25 极度恐慌 / 25-40 偏冷 / 40-60 中性 / 60-75 偏热 / >75 极度贪婪

argument(一句总结)仍由 LLM 撰写,prompt 强制 verdict 已固定,LLM 只能解释"为什么是这个档位"。

输入:SentimentBundle(5 个指标的 当前 / 上期 / rating / unit)
输出:dict { verdict, argument, score, breakdown } 或 None
"""

from __future__ import annotations

import json
import logging
import math
import re

from src.collectors.sentiment import SentimentBundle
from src.processors.llm_client import LLMClient

logger = logging.getLogger(__name__)

_MAX_ARGUMENT_ATTEMPTS = 2
_ARGUMENT_FALLBACK = "指标仍按确定性规则计算，文字说明本期从略。"


# ──────────────────  确定性打分  ──────────────────

_WEIGHTS: dict[str, float] = {
    # 核心层85%:直接反映美股市场情绪与 30 天隐含波动率。
    "CNN Fear & Greed": 0.45,
    "VIX": 0.40,
    # 辅助层15%:用于交叉验证,不得单独决定当日情绪。
    "高收益债利差": 0.08,
    "DXY": 0.05,
    "Shiller PE": 0.02,
}

_PRIMARY_METRICS = frozenset({"CNN Fear & Greed", "VIX"})
_MIN_EFFECTIVE_WEIGHT = 0.50
_MIN_VALID_METRICS = 2


def _piecewise_linear(x: float, points: list[tuple[float, float]]) -> float:
    """分段线性插值:points 必须按 x 升序。x 超出端点时夹到端点 y。"""
    if x <= points[0][0]:
        return points[0][1]
    if x >= points[-1][0]:
        return points[-1][1]
    for i in range(len(points) - 1):
        x1, y1 = points[i]
        x2, y2 = points[i + 1]
        if x1 <= x <= x2:
            t = (x - x1) / (x2 - x1) if x2 > x1 else 0.0
            return y1 + t * (y2 - y1)
    return points[-1][1]


def _finite_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _score_metric(name: str, value: float) -> float | None:
    """把单个指标当前值映射到 0-100 fear-greed 量表。失败返回 None。"""
    value = _finite_float(value)
    if value is None:
        return None
    if name == "CNN Fear & Greed":
        return value if 0 <= value <= 100 else None
    if value <= 0:
        return None
    if name == "VIX":
        # 越低越贪婪。区间映射:8→100,12→90,15→75,20→50,25→30,30→10,40→0
        return _piecewise_linear(value, [(8, 100), (12, 90), (15, 75), (20, 50), (25, 30), (30, 10), (40, 0)])
    if name == "高收益债利差":
        # FRED 单位 %。越低越贪婪:2→90,3→75,4→50,5→35,6→20,8→5
        return _piecewise_linear(value, [(2, 90), (3, 75), (4, 50), (5, 35), (6, 20), (8, 5)])
    if name == "Shiller PE":
        # 历史均值约 17。<15 极度恐慌,>35 极度贪婪
        return _piecewise_linear(value, [(10, 5), (15, 15), (20, 35), (25, 50), (28, 65), (32, 80), (38, 95)])
    if name == "DXY":
        # 美元强对应风险资产偏弱(轻度恐慌),弱美元偏贪婪
        return _piecewise_linear(value, [(90, 75), (95, 60), (100, 50), (105, 35), (110, 20)])
    return None


VERDICT_THRESHOLDS = (
    (25.0, "极度恐慌"),
    (40.0, "偏冷"),
    (60.0, "中性"),
    (75.0, "偏热"),
    (100.1, "极度贪婪"),
)


def _verdict_from_score(score: float) -> str:
    for upper, label in VERDICT_THRESHOLDS:
        if score < upper:
            return label
    return "极度贪婪"


def score_sentiment(bundle: SentimentBundle) -> dict | None:
    """
    确定性加权打分。
    返回 { score: float 0-100, verdict: str, breakdown: list[(name, score, weight)] }。
    bundle 全部指标都失败时返回 None。
    """
    if not bundle or not bundle.metrics:
        return None

    breakdown: list[tuple[str, float, float]] = []
    weighted_sum = 0.0
    weight_total = 0.0
    valid_count = 0
    valid_primary_count = 0
    stale_count = 0
    seen_names: set[str] = set()
    for m in bundle.metrics:
        if m.error:
            continue
        current = _finite_float(m.current)
        if current is None:
            continue
        w = _WEIGHTS.get(m.name)
        if w is None:
            continue
        s = _score_metric(m.name, current)
        if s is None:
            continue
        if m.name in seen_names:
            logger.warning("sentiment.duplicate_metric name=%s", m.name)
            continue
        seen_names.add(m.name)
        # Collection enforces observation age. A cache hit is a transport
        # fact, not a change in market sentiment or evidence date.
        effective_weight = w
        weighted_sum += s * effective_weight
        weight_total += effective_weight
        valid_count += 1
        valid_primary_count += int(m.name in _PRIMARY_METRICS)
        stale_count += int(bool(m.stale_from))
        breakdown.append((m.name, round(s, 1), effective_weight))

    if (
        valid_primary_count == 0
        or valid_count < _MIN_VALID_METRICS
        or weight_total < _MIN_EFFECTIVE_WEIGHT
    ):
        logger.warning(
            "sentiment.insufficient_coverage valid=%d primary=%d "
            "effective_weight=%.3f stale=%d",
            valid_count, valid_primary_count, weight_total, stale_count,
        )
        return None

    score = weighted_sum / weight_total
    return {
        "score": round(score, 1),
        "verdict": _verdict_from_score(score),
        "breakdown": breakdown,
        "coverage": {
            "valid_metrics": valid_count,
            "valid_primary_metrics": valid_primary_count,
            "total_metrics": len(_WEIGHTS),
            "effective_weight_pct": round(weight_total * 100),
            "stale_metrics": stale_count,
        },
    }


# ──────────────────  LLM 写 argument(verdict 已固定,LLM 只解释)  ──────────────────

_SENTENCE_END_RE = re.compile(r"[。！？!?](?:[”’」』】])?")
_ENGLISH_INITIALISM_RE = re.compile(r"(?:\b[A-Za-z]\.){2,}$")
_ENGLISH_TITLE_RE = re.compile(r"\b(?:Mr|Mrs|Ms|Dr|Prof|Sr|Jr|St|No|Fig)\.$", re.IGNORECASE)


def _is_nonterminal_english_period(text: str, index: int) -> bool:
    """识别小数或英文缩写内部的点，避免输出 ``U.`` 一类残句。

    英文缩写的句末语义存在歧义；这里采取保守策略：只要缩写后仍有正文，
    就继续寻找下一个明确句点。多保留文字比截出无法阅读的半个缩写安全。
    """
    before = text[index - 1] if index > 0 else ""
    after = text[index + 1] if index + 1 < len(text) else ""
    if before.isdigit() and after.isdigit():
        return True
    if before.isalpha() and after.isalpha():
        return True

    remaining = text[index + 1:]
    if not remaining.strip():
        return False
    prefix = text[:index + 1]
    return bool(
        _ENGLISH_INITIALISM_RE.search(prefix)
        or _ENGLISH_TITLE_RE.search(prefix)
        or re.search(r"\b[A-Za-z]\.$", prefix)
    )


def one_sentence_summary(value: object) -> str:
    """把模型或历史缓存中的多句总结收束为第一句完整结论。"""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return ""
    match = _SENTENCE_END_RE.search(text)
    if match:
        return text[:match.end()].strip()

    # 英文句点也可作为句末，但小数和缩写内部的点不能截断。
    for match in re.finditer(r"\.", text):
        if not _is_nonterminal_english_period(text, match.start()):
            return text[:match.end()].strip()
    return text

_TASK_INSTRUCTION = """\
任务:基于下列 5 个情绪指标的"当前值 / 上期值 / 变化",**给定固定档位**写 argument(一句中文总结)。

档位由确定性加权算法已经决定,你**不得**改变它。你的工作是:
- 只写一句完整中文总结,建议 35-60 个汉字,句末使用句号;不得拆成第二句
- 在这一句话中解释为什么算法会落到这个档位,引用关键指标的具体数字与方向
- 重点引用 CNN Fear & Greed 与 VIX;高收益债利差、DXY 只能作为辅助印证
- Shiller PE 是慢变量,只能作为长期估值背景轻轻带过;除非它有显著日度变化,不得作为每日情绪判断的主论据
- 没有真实历史分位/区间数据时,不得写"历史极值""历史高位""极端估值""接近泡沫"等绝对化表述
- Shiller PE 允许的最强表述是:"Shiller PE 偏高,提示长期预期收益需克制"
- 提示对应的投资纪律:偏冷/极度恐慌 → "DCA 触发概率上升,保持耐心";偏热/极度贪婪 → "暂缓加仓,守住现金仓位";中性 → 给出留意事项
- 不要写"今日"等时间副词,直接陈述
- 不要 AI 腔,不要"让我们"
- 若某指标标注"(数据源故障,沿用 X 的值)",**不得**把它作为论据主角,只能作为"参考"轻轻带过或干脆不引用;不得写"今日 VIX 上升 / 下降"这类暗示是当天数据的措辞

输出**严格 JSON**(无 markdown 代码块):
{
  "argument": "<一句中文总结>"
}
"""


def _format_input(b: SentimentBundle, fixed_verdict: str, score: float) -> str:
    def _fmt(value: float | None, unit: str) -> str:
        value = _finite_float(value)
        return "—" if value is None else f"{value:.2f}{unit}"

    lines: list[str] = [
        f"已固定档位:今日情绪 · {fixed_verdict}(加权分:{score:.1f}/100)",
        "",
        "5 指标明细:",
    ]
    for m in b.metrics:
        if m.error:
            lines.append(f"- {m.name}: 数据获取失败 ({m.error})")
            continue
        cur = _fmt(m.current, m.unit)
        pri = _fmt(m.prior, m.unit)
        delta = "—"
        delta_value = _finite_float(m.delta)
        if delta_value is not None:
            delta = f"{delta_value:+.2f}{m.unit}"
        rating = f" [{m.rating}]" if m.rating else ""
        # stale_from 非空 = 沿用了 last-known-good 缓存,不是当天数据。
        # 让 LLM 在 argument 中淡化此指标(避免误导用户)。
        stale = f"(数据源故障,沿用 {m.stale_from} 的值)" if m.stale_from else ""
        lines.append(
            f"- {m.name}{rating}: 当前 {cur} | 上期 {pri} | 变化 {delta}{stale}"
            f" | 实际观测 {m.observed_at or '未知'} | 来源 {m.source or '未知'}"
        )
    return "\n".join(lines)


_JSON_RE = re.compile(r"\{[^{}]*\"argument\"[^{}]*\}", re.DOTALL)


def _parse_json(text: str) -> dict | None:
    """容错 JSON 解析:模型偶尔会包 markdown,strip 后再 parse"""
    cleaned = text.strip()
    # 去除 ``` 围栏
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # 尝试找 JSON 子串
        m = _JSON_RE.search(text)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                return None
    return None


def _deterministic_argument(bundle: SentimentBundle, verdict: str) -> str:
    parts = []
    for metric in bundle.metrics:
        if metric.name not in _PRIMARY_METRICS or metric.error or _finite_float(metric.current) is None:
            continue
        label = "CNN 恐惧贪婪指数" if metric.name == "CNN Fear & Greed" else metric.name
        stamp = f"（沿用 {metric.stale_from}）" if metric.stale_from else ""
        number = f"{metric.current:.2f}".rstrip("0").rstrip(".")
        parts.append(f"{label} {number}{metric.unit}{stamp}")
    return "、".join(parts) + f"，按确定性规则综合为{verdict}；留意指标分歧，按既定纪律执行。"


def _argument_supported(text: str, bundle: SentimentBundle, verdict: str) -> bool:
    # A small explanatory sentence has no need for unsupported historical claims
    # or novel numbers. Check every metric clause against that metric, not a bag
    # of numbers from unrelated rows.
    if not text or len(text) > 110 or re.search(r"历史(?:极|新|最|高位|低位)|泡沫|今日|今天", text):
        return False
    if any(label in text for _, label in VERDICT_THRESHOLDS if label != verdict):
        return False
    aliases = {"CNN Fear & Greed": r"CNN|恐惧.*?贪婪", "VIX": r"VIX",
               "DXY": r"DXY|美元指数", "高收益债利差": r"高收益债|信用利差", "Shiller PE": r"Shiller|席勒"}
    mentioned = False
    for clause in re.split(r"[，,；;。]", text):
        numbers = re.findall(r"[+-]?\d+(?:\.\d+)?", clause)
        metrics = [m for m in bundle.metrics if re.search(aliases.get(m.name, r"(?!)"), clause, re.I)]
        if numbers and len(metrics) != 1:
            return False
        for m in metrics:
            mentioned = True
            if m.error or _finite_float(m.current) is None:
                return False
            if m.stale_from and ("参考" not in clause and "沿用" not in clause):
                return False
            allowed = [m.current, m.prior, m.delta]
            if any(not any(v is not None and abs(float(n) - v) <= .011 for v in allowed) for n in numbers):
                return False
            if re.search(r"回落|下降|下跌|收窄", clause) and (m.delta is None or m.delta >= 0):
                return False
            if re.search(r"上升|上涨|走高|扩大|攀升", clause) and (m.delta is None or m.delta <= 0):
                return False
    return mentioned


def judge(
    bundle: SentimentBundle, *, client: LLMClient
) -> dict | None:
    """
    返回 {verdict, argument, score, breakdown}:
    - verdict 由确定性加权打分决定(同输入永远同档位)
    - argument 由 LLM 写,被告知 verdict 已固定只解释为什么
    - LLM 失败时仍返回算法结果,argument 退回模板默认。
    """
    scored = score_sentiment(bundle)
    if not scored:
        return None
    verdict_label = scored["verdict"]
    score = scored["score"]
    payload = _format_input(bundle, verdict_label, score)
    argument = ""
    last_error: str | None = None
    for attempt in range(1, _MAX_ARGUMENT_ATTEMPTS + 1):
        task_instruction = _TASK_INSTRUCTION
        if attempt > 1:
            task_instruction += "\n上一次输出无效；这次只输出包含 argument 的 JSON 对象。"
        resp = client.chat(
            payload,
            task_extra=task_instruction,
            max_tokens=320,
            temperature=0.1,
            timeout=20,
            thinking=False,
        )
        if resp.text:
            data = _parse_json(resp.text)
            if data and "argument" in data and str(data["argument"]).strip():
                candidate = one_sentence_summary(data["argument"])
                if _argument_supported(candidate, bundle, verdict_label):
                    argument = candidate
                    break
                last_error = "UnsupportedArgument"
                logger.warning("sentiment_judge.unsupported_argument attempt=%d", attempt)
                continue
            last_error = "InvalidJSONOrEmptyArgument"
            logger.warning(
                "sentiment_judge.parse_failed attempt=%d/%d text=%r",
                attempt, _MAX_ARGUMENT_ATTEMPTS, resp.text[:200],
            )
        else:
            last_error = resp.error or "EmptyOutput"
            logger.warning(
                "sentiment_judge.llm_failed attempt=%d/%d reason=%s",
                attempt, _MAX_ARGUMENT_ATTEMPTS, last_error,
            )

    argument_fallback = not argument
    if argument_fallback:
        argument = _deterministic_argument(bundle, verdict_label)

    logger.info(
        "sentiment_judge.ok verdict=%r score=%.1f argument_chars=%d",
        verdict_label, score, len(argument),
    )
    return {
        "verdict": f"今日情绪 · {verdict_label}",
        "argument": argument,
        "score": score,
        "breakdown": scored["breakdown"],
        "coverage": scored["coverage"],
        "argument_fallback": argument_fallback,
        "argument_error": last_error if argument_fallback else None,
    }
