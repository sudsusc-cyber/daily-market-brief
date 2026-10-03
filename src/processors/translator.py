"""
英文标题批量中文翻译。从 utils/translate.py 整合到 processors/(M4)。

用 LLMClient 统一接口,token 计入累积统计。
单次 prompt 打包 N 条,按 ▦ N: 编号,降低 round-trip 成本。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable

from src.processors.llm_client import LLMClient
from src.processors.news_selection import chinese_prose, factual_excerpt, plain_source
from src.processors.presentation_vocabulary import _LOCALIZED_TERMS_V9
from src.processors.translation_guard import translation_errors

logger = logging.getLogger(__name__)


_LINE_RE = re.compile(r"^▦\s*(\d+)\s*:\s*(.+?)\s*$")
_MAX_ATTEMPTS = 2

_TASK_INSTRUCTION = """\
任务:把下面以 "▦ N:" 编号的英文财经新闻完整证据片段逐条翻译为简体中文。
约束:
- 严格保留 "▦ N: <译文>" 格式,每条独占一行
- 公司、人名、产品型号保留原写；地名和机构名按下方统一术语表使用通行中文，未列出的专名保留原写，不猜译
- 原文所有 ticker 和缩写（AI、TPU、NASDAQ 等）保留，英文可紧邻中文
- 数字、日期、百分号保持原样；金额单位可转为中文但金额、币种不得变化
- 保留 AI 技术方法：distillation 译为蒸馏，fine-tuning 译为微调；不得泛化为使用，也不得凭空补充这些方法
- 财经习语按完整句意译成自然中文，不照搬比喻；make peace with 市场变化是逐渐适应，不是和解。普通技术术语译为中文，如 tensor processing unit 为张量处理器，bug 为故障；保留原有缩写。
- 使用自然中文语序；时间或风险“looms for”市场/资产时，译为该市场/资产“面临”或“迎来”相应时期/风险，不要写成“十月逼近国债”等字面语序；不得添加因果连接
- 按事件语境译词：investigate 是调查，declined to 是拒绝；government debt rout 是国债遭抛售、债券价格下跌，不是政府债务规模下降。
- 多义词依据动作对象翻译：服务/信号/数据的 loss 是中断或丢失，不是财务亏损；release 储备是释放/投放，release 人员是释放，release 产品是发布；cut a check 是开具支票/支付，不是削减支票。不得把这些不同事件混为一谈。
- 保留主体数量范围；复数的最大/主要经济体、公司、银行等须译出“几个”“多个”“各”等，不能缩成一个主体
- 回购金额必须保留口径：additional 为新增授权，remaining 为剩余授权额度；批准回购不等于已经执行回购
- 文件类型必须准确：summary of opinions 是意见摘要，minutes 是会议纪要；普通 summary 不可译成会议纪要
- 完整翻译，不概括、不补充判断；严格保留否定、可能/计划/待批等限定和事件状态
- 输出仅这些行,不要任何前言、解释、Markdown
"""

_TASK_INSTRUCTION += "\n统一术语表：" + "；".join(f"{en}={zh}" for en, zh in _LOCALIZED_TERMS_V9.items())


def _is_chinese(text: str) -> bool:
    # Use the publication language rule too: a Chinese name/publisher inside an
    # English report does not make the whole report an already translated item.
    return chinese_prose(text)


def _parse_lines(text: str) -> dict[int, str]:
    out: dict[int, str] = {}
    for line in text.splitlines():
        m = _LINE_RE.match(line.strip())
        if not m:
            continue
        try:
            out[int(m.group(1))] = m.group(2).strip()
        except ValueError:
            continue
    return out


def translate_titles(
    titles: list[str],
    *,
    client: LLMClient,
    batch_size: int = 30,
    diagnostics: dict | None = None,
    max_attempts: int = _MAX_ATTEMPTS,
    timeout: float | None = None,
) -> list[str]:
    """翻译一批标题。已是中文的跳过,失败的位置回退原文。"""
    if not titles:
        return []
    to_translate = [(i, t) for i, t in enumerate(titles) if t and not _is_chinese(t)]
    if not to_translate:
        return list(titles)

    out = list(titles)
    for start in range(0, len(to_translate), batch_size):
        chunk = to_translate[start : start + batch_size]
        indexed_chunk = {
            position: pair
            for position, pair in enumerate(chunk, start=1)
        }
        pending = set(indexed_chunk)
        translated: dict[int, str] = {}
        rejected: dict[int, list[str]] = {position: diagnostics[pair[0]].get("errors", [])
                                        for position, pair in indexed_chunk.items()
                                        if diagnostics and pair[0] in diagnostics}

        for attempt in range(1, max_attempts + 1):
            numbered = "\n".join(
                f"▦ {position}: {indexed_chunk[position][1]}" + (f"\n上次译文未保留: {rejected[position]}；保留原始缩写、数字/日期、主体顺序及限定语。" if position in rejected else "")
                for position in sorted(pending)
            )
            resp = client.chat(
                numbered,
                task_extra=_TASK_INSTRUCTION,
                max_tokens=3500,
                temperature=0.0,
                thinking=False,
                **({"timeout": timeout} if timeout is not None else {}),
            )
            if not resp.text:
                logger.warning(
                    "translate.batch_attempt_failed start=%d size=%d attempt=%d reason=%s",
                    start, len(chunk), attempt, resp.error,
                )
            else:
                parsed = _parse_lines(resp.text)
                for position in pending:
                    text = parsed.get(position, "").strip()
                    errors = translation_errors(indexed_chunk[position][1], text) if text else ["empty"]
                    if text and not errors:
                        translated[position] = text
                        if diagnostics is not None:
                            diagnostics.pop(indexed_chunk[position][0], None)
                    elif text:
                        rejected[position] = errors
                        if diagnostics is not None:
                            diagnostics[indexed_chunk[position][0]] = {"errors": errors, "candidate": text[:600]}
                        logger.warning("translate.rejected index=%d reasons=%s", position, errors)
                pending.difference_update(translated)

            if not pending:
                break
            if attempt < max_attempts:
                logger.warning(
                    "translate.batch_retry start=%d size=%d missing=%d attempt=%d",
                    start, len(chunk), len(pending), attempt + 1,
                )

        for position, text in translated.items():
            orig_idx, _ = indexed_chunk[position]
            out[orig_idx] = text

        if pending:
            logger.warning(
                "translate.batch_incomplete start=%d size=%d parsed=%d/%d missing=%s",
                start, len(chunk), len(translated), len(chunk), sorted(pending),
            )
        else:
            logger.info(
                "translate.batch_ok start=%d size=%d parsed=%d/%d",
                start, len(chunk), len(translated), len(chunk),
            )
    return out


def translate_in_place_news(items: Iterable, *, client: LLMClient, max_attempts: int = _MAX_ATTEMPTS, timeout: float | None = None) -> None:
    """保留原文，单独保存核验用完整译文；邮件行文由 grounded_text 整理。"""
    items_list = list(items)
    excerpts = [factual_excerpt(it) for it in items_list]
    diagnostics = {i: it.translation_diagnostic for i, it in enumerate(items_list)
                   if getattr(it, "translation_diagnostic", None)}
    translated = translate_titles(excerpts, client=client, diagnostics=diagnostics,
                                  max_attempts=max_attempts, timeout=timeout)
    for index, (it, original, text) in enumerate(zip(items_list, excerpts, translated, strict=True)):
        it.translation_diagnostic = diagnostics.get(index, {})
        it.source_excerpt = original
        it.translated_excerpt = text
        it.translated_title = text if original == plain_source(it.title) else ""
