"""
中英混排文本工具。

主要功能:在中文方块字与英文/数字之间插入 thin space(U+2009),
消除"中文 + 英文细字母"的"补丁感"。

例:'Apple暂停App Store' → 'Apple 暂停 App Store'(用 thin space 而非普通空格)
"""

from __future__ import annotations

import re

# Thin space (U+2009) — 比普通空格窄,中英交界处视觉自然
THIN_SPACE = " "

# 中文字符与英文/数字之间需要 thin space
_CN_EN = re.compile(r"([一-龥])([a-zA-Z0-9])")
_EN_CN = re.compile(r"([a-zA-Z0-9])([一-龥])")


def add_cjk_spacing(text: str | None) -> str:
    """在中文与英文/数字交界处插入 thin space。None / 空串原样返回。"""
    if not text:
        return text or ""
    text = _CN_EN.sub(r"\1" + THIN_SPACE + r"\2", text)
    text = _EN_CN.sub(r"\1" + THIN_SPACE + r"\2", text)
    return text
