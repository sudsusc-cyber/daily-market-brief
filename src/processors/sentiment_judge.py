"""
情绪综合判断(模块 5 加工)。

输入:SentimentBundle(6 个指标的 当前 / 一周前 / rating / unit)
输出:dict { verdict: "今日情绪 · 偏热", argument: "<2-3 句>" }
失败时返回 None,模板降级到原始指标小表。

prompt 要求 LLM 输出严格 JSON,便于稳定解析。
"""

from __future__ import annotations

import json
import logging
import re

from src.collectors.sentiment import SentimentBundle
from src.processors.llm_client import LLMClient

logger = logging.getLogger(__name__)


_TASK_INSTRUCTION = """\
任务:基于下列 6 个情绪指标的"当前值 / 一周前值 / 变化",输出**严格 JSON**(无任何前言、解释、代码块标记)。

JSON 结构:
{
  "verdict": "今日情绪 · <偏冷|中性|偏热|极度恐慌|极度贪婪> 中的一个",
  "argument": "2-3 句中文论据,引用关键数字"
}

约束:
- argument 要联系开源的投资框架:若偏冷或极度恐慌,提示"DCA 信号触发概率上升,可保持耐心"
  之类;若偏热或极度贪婪,提示"建议暂缓加仓,守住现金仓位";中性给出留意事项
- 数字保留 1-2 位小数即可
- 不要写"今日"等时间副词,直接陈述
- 不要 AI 腔
- 输出**只有 JSON 一个对象**,不要 markdown ```json 包裹
"""


def _format_input(b: SentimentBundle) -> str:
    lines: list[str] = []
    for m in b.metrics:
        if m.error:
            lines.append(f"- {m.name}: 数据获取失败 ({m.error})")
            continue
        cur = "—" if m.current is None else f"{m.current:.2f}{m.unit}"
        pri = "—" if m.prior is None else f"{m.prior:.2f}{m.unit}"
        delta = "—"
        if m.delta is not None:
            delta = f"{m.delta:+.2f}{m.unit}"
        rating = f" [{m.rating}]" if m.rating else ""
        lines.append(f"- {m.name}{rating}: 当前 {cur} | 一周前 {pri} | 变化 {delta}")
    return "\n".join(lines)


_JSON_RE = re.compile(r"\{[^{}]*\"verdict\"[^{}]*\}", re.DOTALL)


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
    """成功返回 {verdict, argument},失败返回 None"""
    if not bundle or not bundle.metrics:
        return None
    payload = _format_input(bundle)
    resp = client.chat(
        payload,
        task_extra=_TASK_INSTRUCTION,
        max_tokens=400,
        temperature=0.2,
    )
    if not resp.text:
        logger.warning("sentiment_judge.failed reason=%s", resp.error)
        return None
    data = _parse_json(resp.text)
    if not data or "verdict" not in data or "argument" not in data:
        logger.warning("sentiment_judge.parse_failed text=%r", resp.text[:200])
        return None
    logger.info("sentiment_judge.ok verdict=%r argument_chars=%d",
               data.get("verdict"), len(str(data.get("argument", ""))))
    return {
        "verdict": str(data["verdict"]).strip(),
        "argument": str(data["argument"]).strip(),
    }
