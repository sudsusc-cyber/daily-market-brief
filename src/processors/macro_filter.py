"""
宏观新闻头条 → 头版级别筛选 + 一段叙述(模块 4 加工)。

输入:list[MacroFeedBundle](标题已被 translator 翻成中文)
输出:str(150-300 字段落),失败时返回 None,上层降级到原始列表展示
"""

from __future__ import annotations

import logging

from src.collectors.macro_news import MacroFeedBundle
from src.processors.llm_client import LLMClient

logger = logging.getLogger(__name__)


_TASK_INSTRUCTION = """\
任务:从下列过去 24 小时各财经媒体头条中,筛选出**真正影响全球市场或重大经济**的头版级新闻,
然后用一段中文叙述总结(150-300 字)。

仅纳入:
- 央行政策(美联储 / 欧央行 / 日央行 / 人民银行)
- 重大地缘政治事件
- 影响万亿级资产的监管变动
- 宏观数据(非农 / CPI / GDP / PMI)
- 系统性风险事件(银行危机、主权违约、能源价格冲击)

排除:
- 个股新闻、行业评论
- 人物花边
- "分析师认为 / 高盛预测"等二手观点
- 列表 / 排行类文章

输出格式:
- 一段连续叙述,不要列表
- 不写"今日 / 过去 24 小时"等模糊时间
- 不写引言、不写"综上"
- 输出**只**这一段叙述
"""


def _format_input(bundles: list[MacroFeedBundle]) -> str:
    lines: list[str] = []
    for b in bundles:
        if b.error or not b.items:
            continue
        lines.append(f"【{b.source}】")
        for it in b.items[:8]:  # 每源最多 8 条进 LLM 视野(模板只显示前 5)
            lines.append(f"  · {it.title}")
    return "\n".join(lines)


def summarize(
    bundles: list[MacroFeedBundle],
    *,
    client: LLMClient,
) -> str | None:
    if not bundles:
        return None
    payload = _format_input(bundles)
    if not payload.strip():
        return None
    resp = client.chat(
        payload,
        task_extra=_TASK_INSTRUCTION,
        max_tokens=700,
        temperature=0.3,
    )
    if not resp.text:
        logger.warning("macro_filter.failed reason=%s", resp.error)
        return None
    logger.info("macro_filter.ok chars=%d", len(resp.text))
    return resp.text
