"""
持仓公司昨日新闻 → 一段叙述(模块 1 加工)。

输入:list[CompanyNewsBundle]
输出:str(段落叙述,200-400 字),失败时返回 None,上层降级到原始列表展示
"""

from __future__ import annotations

import logging

from src.collectors.company_news import CompanyNewsBundle
from src.processors.llm_client import LLMClient

logger = logging.getLogger(__name__)


_TASK_INSTRUCTION = """\
任务:把下面 12 家持仓公司"昨日新闻列表"加工成一段中文叙述。
约束:
- **第三人称**,平铺直叙
- 每家公司至多 1-2 句,**没有重要新闻就直接跳过**(不要写"无新闻")
- 重点关注:业绩、合作、监管、产品发布、人事变动、资本动作
- 忽略:股价波动本身、"分析师上调评级"、"列表 / 排行"型文章、KOL 评论
- 长度 200-400 字,自然分段(可不分段)
- 不写"昨日"等模糊时间,具体到事件即可
- 不写引言、不写结语、不要"以下是 / 综上所述"
- 输出**只**这一段叙述,不要标题不要小标题
"""


def _format_input(bundles: list[CompanyNewsBundle]) -> str:
    """把 bundle 拼成 LLM 友好的多行格式,只取每家前 5 条"""
    lines: list[str] = []
    for b in bundles:
        if b.error:
            continue
        head = f"【{b.holding.ticker}({b.holding.name})】"
        if not b.items:
            lines.append(f"{head} 无")
            continue
        lines.append(head)
        for it in b.items[:5]:
            src = f" — {it.source}" if it.source else ""
            lines.append(f"  · {it.title}{src}")
    return "\n".join(lines)


def summarize(
    bundles: list[CompanyNewsBundle],
    *,
    client: LLMClient,
) -> str | None:
    """成功返回段落字符串,失败返回 None"""
    if not bundles:
        return None
    payload = _format_input(bundles)
    if not payload.strip():
        return None
    resp = client.chat(
        payload,
        task_extra=_TASK_INSTRUCTION,
        max_tokens=900,
        temperature=0.4,
    )
    if not resp.text:
        logger.warning("news_summarizer.failed reason=%s", resp.error)
        return None
    logger.info("news_summarizer.ok chars=%d", len(resp.text))
    return resp.text
