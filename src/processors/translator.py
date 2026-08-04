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

logger = logging.getLogger(__name__)


_HAS_CJK = re.compile(r"[一-鿿]")
_LINE_RE = re.compile(r"^▦\s*(\d+)\s*:\s*(.+?)\s*$")
_MAX_ATTEMPTS = 2

_TASK_INSTRUCTION = """\
任务:把下面以 "▦ N:" 编号的英文财经新闻标题逐条翻译为简体中文。
约束:
- 严格保留 "▦ N: <译文>" 格式,每条独占一行
- 公司 / 人名 / 产品名(Microsoft / Buffett / iPhone)保留英文原写
- 数字、日期、百分号保持原样
- 输出仅这些行,不要任何前言、解释、Markdown
"""


def _is_chinese(text: str) -> bool:
    return bool(_HAS_CJK.search(text))


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

        for attempt in range(1, _MAX_ATTEMPTS + 1):
            numbered = "\n".join(
                f"▦ {position}: {indexed_chunk[position][1]}"
                for position in sorted(pending)
            )
            resp = client.chat(
                numbered,
                task_extra=_TASK_INSTRUCTION,
                max_tokens=3500,
                temperature=0.0,
                thinking=False,
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
                    if text:
                        translated[position] = text
                pending.difference_update(translated)

            if not pending:
                break
            if attempt < _MAX_ATTEMPTS:
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


def translate_in_place_news(items: Iterable, *, client: LLMClient) -> None:
    """把 NewsItem / FigureMention / MacroNewsItem 等对象的 .title 替换为中文。"""
    items_list = list(items)
    titles = [getattr(it, "title", "") for it in items_list]
    translated = translate_titles(titles, client=client)
    for it, t in zip(items_list, translated, strict=True):
        it.title = t
