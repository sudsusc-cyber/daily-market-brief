"""
情绪综合判断(模块 5 加工)。

verdict(温度档位)由**确定性加权打分**得出,同样输入永远同样档位:
  - 5 指标各打 0-100 分(0=极度恐慌,100=极度贪婪)
  - 加权平均(失败指标从权重中剔除并重新归一化)
  - 阈值切档:<25 极度恐慌 / 25-40 偏冷 / 40-60 中性 / 60-75 偏热 / >75 极度贪婪

argument(2-3 句论据)仍由 LLM 撰写,prompt 强制 verdict 已固定,LLM 只能解释"为什么是这个档位"。

输入:SentimentBundle(5 个指标的 当前 / 前一日 / rating / unit)
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


# ──────────────────  确定性打分  ──────────────────

_WEIGHTS: dict[str, float] = {
    # 在 PR #38 去 RSI 后的权重(0.284/0.318/0.250/0.057/0.091)基础上,
    # 应用 codex 2d4c490 的"Shiller PE 降权 50%"意图:
    # Shiller 0.057 → 0.030,腾出的 0.027 按现有比例分摊给其余 4 项后归一化。
    "CNN Fear & Greed": 0.290,
    "VIX": 0.325,
    "高收益债利差": 0.260,
    "Shiller PE": 0.030,
    "DXY": 0.095,
}


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
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _score_metric(name: str, value: float) -> float | None:
    """把单个指标当前值映射到 0-100 fear-greed 量表。失败返回 None。"""
    value = _finite_float(value)
    if value is None:
        return None
    if name == "CNN Fear & Greed":
        return max(0.0, min(100.0, value))
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
        weighted_sum += s * w
        weight_total += w
        breakdown.append((m.name, round(s, 1), w))

    if weight_total == 0.0:
        return None

    score = weighted_sum / weight_total
    return {
        "score": round(score, 1),
        "verdict": _verdict_from_score(score),
        "breakdown": breakdown,
    }


# ──────────────────  LLM 写 argument(verdict 已固定,LLM 只解释)  ──────────────────

_TASK_INSTRUCTION = """\
任务:基于下列 5 个情绪指标的"当前值 / 前一日值 / 变化",**给定固定档位**写 argument(2-3 句中文论据)。

档位由确定性加权算法已经决定,你**不得**改变它。你的工作是:
- 用 2-3 句话解释为什么算法会落到这个档位,引用关键指标的具体数字与方向
- 重点引用 CNN Fear & Greed / VIX / 高收益债利差 这类更贴近日频风险偏好的指标
- Shiller PE 是慢变量,只能作为长期估值背景轻轻带过;除非它有显著日度变化,不得作为每日情绪判断的主论据
- 没有真实历史分位/区间数据时,不得写"历史极值""历史高位""极端估值""接近泡沫"等绝对化表述
- Shiller PE 允许的最强表述是:"Shiller PE 偏高,提示长期预期收益需克制"
- 提示对应的投资纪律:偏冷/极度恐慌 → "DCA 触发概率上升,保持耐心";偏热/极度贪婪 → "暂缓加仓,守住现金仓位";中性 → 给出留意事项
- 不要写"今日"等时间副词,直接陈述
- 不要 AI 腔,不要"让我们"
- 若某指标标注"(数据源故障,沿用 X 的值)",**不得**把它作为论据主角,只能作为"参考"轻轻带过或干脆不引用;不得写"今日 VIX 上升 / 下降"这类暗示是当天数据的措辞

输出**严格 JSON**(无 markdown 代码块):
{
  "argument": "<2-3 句中文论据>"
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
            f"- {m.name}{rating}: 当前 {cur} | 前一日 {pri} | 变化 {delta}{stale}"
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
    resp = client.chat(
        payload,
        task_extra=_TASK_INSTRUCTION,
        max_tokens=1500,
        temperature=0.2,
    )
    argument = ""
    if resp.text:
        data = _parse_json(resp.text)
        if data and "argument" in data:
            argument = str(data["argument"]).strip()
        else:
            logger.warning("sentiment_judge.parse_failed text=%r", resp.text[:200])
    else:
        logger.warning("sentiment_judge.llm_failed reason=%s", resp.error)

    logger.info(
        "sentiment_judge.ok verdict=%r score=%.1f argument_chars=%d",
        verdict_label, score, len(argument),
    )
    return {
        "verdict": f"今日情绪 · {verdict_label}",
        "argument": argument,
        "score": score,
        "breakdown": scored["breakdown"],
    }
