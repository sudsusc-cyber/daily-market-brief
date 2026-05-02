"""
主题输出验证(Step 6)。

按 plan 给的 5 条规则严格校验:
1. 长度严格 9 字符(8 字 + 1 全角空格)
2. 第 5 字符必须是全角空格 U+3000
3. 前 4 字 + 后 4 字必须全部是 CJK 中文
4. 不得含禁词列表
5. 顺带:不含 ASCII 数字/英文/常见标点
6. 季节意象一致性(若调用方传入 season):谷雨写蝉鸣这种明显违和直接拒绝
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


# 季节意象禁忌:每个季节明显违和的字 / 短词,出现即拒。
# 设计取舍:宁可漏判(不在表内的细微违和放过),不可错杀(谷雨写蝉鸣这种必须拦)。
# 整段拼接后逐 substring 匹配,命中任一即 fail。
SEASON_TABOOS: dict[str, tuple[str, ...]] = {
    # 春(立春~谷雨):春日不该有蝉、雪、霜、冰、烈火、凋零等冬夏意象
    "spring": ("蝉", "雪", "霜", "冰", "凋零", "烈火", "焦阳", "炎天", "炎云", "阳烈"),
    # 夏(立夏~大暑):夏日不该有雪、霜、冰、寒江、寒林、凋零等冬秋意象
    "summer": ("雪", "霜", "冰", "凋零", "寒江", "寒林", "寒鸦", "孤舟寒"),
    # 秋(立秋~霜降):秋日不该有烈火/焦阳的盛夏感,也不该有玄冰的深冬感
    "autumn": ("烈火", "焦阳", "炎天", "炎云", "阳烈", "玄冰"),
    # 冬(立冬~大寒):冬日不该有蝉、烈火、阳烈、炎云、春潮等夏春意象
    "winter": ("蝉", "烈火", "焦阳", "炎天", "炎云", "阳烈", "春潮"),
}

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


def validate(subject: str, season: str | None = None) -> tuple[bool, str]:
    """
    返回 (是否通过, 详细原因)。失败时原因供 fallback 用作 LLM 纠错反馈。
    season 参数(可选):"spring"/"summer"/"autumn"/"winter"。传入则做季节意象禁忌检查。
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

    # 6. 季节意象一致性(可选 — 仅当 season 已知时启用)
    if season:
        ok, reason = validate_season_imagery(s, season)
        if not ok:
            return False, reason

    return True, "ok"


def validate_season_imagery(subject: str, season: str) -> tuple[bool, str]:
    """季节意象一致性校验。命中任一禁忌字 → 拒绝并给出可读原因(供 LLM 重试反馈)。

    例:谷雨(spring)+「蝉鸣未歇」→ False, "春日意象禁忌:蝉(春天无蝉鸣,属夏意象)"
    """
    taboos = SEASON_TABOOS.get(season)
    if not taboos:
        return True, "ok"
    for taboo in taboos:
        if taboo in subject:
            season_cn = {"spring": "春", "summer": "夏",
                         "autumn": "秋", "winter": "冬"}.get(season, season)
            return False, f"{season_cn}日意象禁忌:出现 {repr(taboo)} 是异季节意象,不应在{season_cn}季使用"
    return True, "ok"


def is_valid(subject: str, season: str | None = None) -> bool:
    """便捷布尔接口。"""
    return validate(subject, season=season)[0]
