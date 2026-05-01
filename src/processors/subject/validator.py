"""
主题输出验证(Step 6)。

按 plan 给的 5 条规则严格校验:
1. 长度严格 9 字符(8 字 + 1 全角空格)
2. 第 5 字符必须是全角空格 U+3000
3. 前 4 字 + 后 4 字必须全部是 CJK 中文
4. 不得含禁词列表
5. 顺带:不含 ASCII 数字/英文/常见标点
"""

from __future__ import annotations

# plan 列出的禁词
BANNED_WORDS: frozenset[str] = frozenset({
    "涨", "跌", "震", "盘",
    "市场", "股市", "股票", "股价", "指数", "基金",
    "仓位", "交易", "买入", "卖出",
    "苹果", "微软", "英伟", "谷歌", "腾讯", "玛特",
    "好市", "可口", "运通", "穆迪", "台积",
})

# 标点 / 特殊符号 / 数字 / 英文一律拒绝
_FORBIDDEN_CHAR_RANGES = [
    ("0", "9"),       # ASCII 数字
    ("a", "z"),       # ASCII 小写
    ("A", "Z"),       # ASCII 大写
]
_FORBIDDEN_PUNCT = set(",。、;:·「」『』《》\"'!?,.;:'\"!?")


def _is_cjk(ch: str) -> bool:
    """是否 CJK Unified Ideographs(基本汉字)。"""
    return "一" <= ch <= "鿿"


def validate(subject: str) -> tuple[bool, str]:
    """
    返回 (是否通过, 详细原因)。失败时原因供 fallback 用作 LLM 纠错反馈。
    """
    s = (subject or "").strip()

    # 1. 长度
    if len(s) != 9:
        return False, f"长度错误:实际 {len(s)} 字符,应为 9"

    # 2. 第 5 字符是全角空格
    if s[4] != "　":
        return False, f"第 5 字符应为全角空格 U+3000,实际为 {repr(s[4])}"

    # 3. 前 4 字 + 后 4 字都是 CJK
    chars = s[:4] + s[5:]
    for i, ch in enumerate(chars):
        if not _is_cjk(ch):
            pos = i if i < 4 else i + 1  # 还原原字符串位置
            return False, f"非中文字符 {repr(ch)} 在第 {pos+1} 位"

    # 4. 禁词
    for word in BANNED_WORDS:
        if word in s:
            return False, f"含禁词 {repr(word)}"

    # 5. 标点检查(前面 CJK 检查已能拦截,但二重保险)
    for ch in s:
        if ch in _FORBIDDEN_PUNCT:
            return False, f"含标点 {repr(ch)}"

    return True, "ok"


def is_valid(subject: str) -> bool:
    """便捷布尔接口。"""
    return validate(subject)[0]
