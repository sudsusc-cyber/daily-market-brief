"""
英文标题批量中文翻译(M3 补丁,M4 起会被 processors/llm_client.py 替代)。

设计:
- 一次 prompt 把 N 个标题打包送 DeepSeek,模型按行返回译文,降低 round-trip 成本
- 已是中文的标题(港股 Google News 中文 / 9992.HK / 0700.HK 来源)无需翻译
- 失败回退:LLM 调用失败时返回原文,模板照常显示英文标题(模板已能容纳混排)
"""

from __future__ import annotations

import logging
import re
from typing import Iterable

from openai import OpenAI

logger = logging.getLogger(__name__)


_HAS_CJK = re.compile(r"[一-鿿]")


def _is_chinese(text: str) -> bool:
    """简单启发:含中文字符即视为已是中文,跳过翻译"""
    return bool(_HAS_CJK.search(text))


_BATCH_PROMPT = """你是一名财经新闻翻译。把下面用 ▦ 编号的英文标题逐条翻译为简体中文。
约束:
- 严格保留编号格式 "▦ N: <译文>",每条独占一行
- 译文要忠实、紧凑,不增添未出现的信息
- 公司 / 人名 / 产品名 (如 Microsoft / Buffett / iPhone) 保留英文原写
- 数字、日期、百分号保持原样
- 输出**仅**这些行,不要任何解释、前言、Markdown

英文标题:
{titles}
"""


_LINE_RE = re.compile(r"^▦\s*(\d+)\s*:\s*(.+?)\s*$")


def translate_titles(
    titles: list[str],
    *,
    api_key: str,
    model: str = "deepseek-v4-flash",
    base_url: str = "https://api.deepseek.com",
    batch_size: int = 30,
    timeout: int = 60,
) -> list[str]:
    """
    把 titles 列表翻译为中文。返回与输入等长的列表。
    已是中文的元素原样返回。任何错误都不向上抛,失败的元素回退原文。
    """
    if not titles:
        return []

    # 标记哪些位置需要翻译
    to_translate: list[tuple[int, str]] = [
        (i, t) for i, t in enumerate(titles) if t and not _is_chinese(t)
    ]
    if not to_translate:
        return list(titles)

    out = list(titles)  # 浅拷贝
    client = OpenAI(api_key=api_key, base_url=base_url)

    # 分批调用
    for start in range(0, len(to_translate), batch_size):
        chunk = to_translate[start : start + batch_size]
        numbered = "\n".join(f"▦ {i+1}: {t}" for i, (_, t) in enumerate(chunk))
        prompt = _BATCH_PROMPT.format(titles=numbered)
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=2048,
                temperature=0.0,
                timeout=timeout,
            )
        except Exception as exc:  # noqa: BLE001 — 翻译失败不应阻断邮件发送
            logger.exception("translate.batch_failed start=%d size=%d", start, len(chunk))
            logger.warning("translate.fallback_to_original count=%d reason=%s", len(chunk), exc)
            continue

        text = (resp.choices[0].message.content or "").strip() if resp.choices else ""
        parsed = _parse_lines(text)
        # 把解析出的译文按 chunk 内顺序回填
        for i, (orig_idx, original) in enumerate(chunk, start=1):
            translated = parsed.get(i)
            if translated:
                out[orig_idx] = translated
            # 失败的位置保留 out[orig_idx] 原值即原文

        used = getattr(resp, "usage", None)
        logger.info(
            "translate.batch_ok start=%d size=%d parsed=%d/%d usage=%s",
            start, len(chunk), len(parsed), len(chunk), used,
        )

    return out


def _parse_lines(text: str) -> dict[int, str]:
    """从 LLM 回复里解析 ▦ N: 译文 格式,返回 {N: 译文}"""
    out: dict[int, str] = {}
    for line in text.splitlines():
        m = _LINE_RE.match(line.strip())
        if m:
            try:
                idx = int(m.group(1))
                out[idx] = m.group(2).strip()
            except ValueError:
                continue
    return out


def translate_in_place_news(
    items: Iterable, *, api_key: str
) -> None:
    """
    便利函数:把 NewsItem / FigureMention / MacroNewsItem 之类对象的 .title 字段
    就地替换为中文译文(若原本是英文)。
    """
    items_list = list(items)
    titles = [getattr(it, "title", "") for it in items_list]
    translated = translate_titles(titles, api_key=api_key)
    for it, t in zip(items_list, translated):
        it.title = t
