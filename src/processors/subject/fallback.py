"""
主题生成兜底(Step 7)。

按层级降级,确保任何情况下都能给出 8 字主题(主题生成永不阻塞邮件发送):
- LLM 失败 / 验证失败 → 走兜底静态模板
- 节气短语 phrase 由 solar_terms.py 自动给出(谷雨春深 等)

兜底库每种情境提供 6-8 个变体,按日期 hash 确定性选取
(同情境跨多日不同输出,但同一日同情境永远同输出 → 缓存友好)。
"""

from __future__ import annotations

import hashlib

from src.processors.subject.extractor import SubjectData
from src.processors.subject.solar_terms import season_of
from src.processors.subject.validator import validate

# ──────────────  后 4 字兜底库(每种情境 6-8 个变体)  ──────────────

LUMP_SUM_VARIANTS = [
    "重锚下水", "重金筑基", "深谷布局",
    "广厦立柱", "潜龙跃渊", "玄海投锚",
    "磐石沉渊", "金锚定洋", "深掘寒泉", "万钧压底",
]

MULTI_DCA_VARIANTS = [  # ≥3 只 DCA
    "拾贝缓行", "循阶而上", "聚沙成塔",
    "采星渐行", "捧珠而归", "拾穗踏歌",
    "积玉成山", "携手登阶", "踏雪寻梅",
]

SINGLE_DCA_VARIANTS = [  # 1-2 只 DCA
    "缓步入舟", "轻锚试水", "薄云布雨",
    "投石问路", "微澜入舟", "暗渡微澜",
    "点墨成线", "步月寻江", "悄然落子", "雨打芭蕉",
]

EXTREME_GREED_VARIANTS = [
    "烈火烹油", "万物焦阳", "潮卷千舟",
    "玉宇生焰", "阳烈如焚", "烟云蔽日",
    "长风破浪", "明火未熄", "炎天似炙",
]

EXTREME_FEAR_VARIANTS = [
    "万木凋零", "霜风骤起", "玄冰封地",
    "肃杀之气", "寒鸦盘空", "冷月无声",
    "霜寒万里", "雪压千山", "月落乌啼", "孤舟寒江",
]

WARM_VARIANTS = [  # 偏热(去掉所有"渐 X"、"持仓 X"、"守仓 X")
    "炎云未散", "卧听涛声", "稳钓寒江",
    "坐观云起", "闲云野鹤", "月窥金鼎",
    "玉宇微温", "云霞蔽日", "蝉鸣未歇",
]

COLD_VARIANTS = [  # 偏冷
    "守寒待春", "潜龙在渊", "雪覆山前",
    "冷月静观", "风雪藏锋", "蛰伏听雷",
    "寒林独行", "玄冰守渊", "暮雪推门",
    "独钓寒江", "孤灯照雪",
]

NEUTRAL_VARIANTS = [  # 中性 + 全静默(去掉"持仓 X"、"守仓 X"白话)
    "按兵不动", "镜湖如旧", "秋水长天",
    "清风徐来", "淡看潮汐", "稳坐钓台",
    "独钓江雪", "闲看云起", "月静风疏",
    "秋水悠然", "闲钓寒江", "白云出岫",
]


# ──────────────  确定性选取  ──────────────

def _pick(variants: list[str], seed_key: str) -> str:
    """按 seed_key(通常是 today_iso + 情境名)选取 variants 里的一个。
    SHA1 hash 保证同 seed → 同输出(缓存友好),跨日期 → 不同输出。"""
    h = hashlib.sha1(seed_key.encode("utf-8"), usedforsecurity=False).digest()
    idx = int.from_bytes(h[:4], "big") % len(variants)
    return variants[idx]


def _pick_valid(
    *, phrase: str, variants: list[str], seed_key: str, season: str | None,
) -> str:
    """从 hash 起点循环，确定性选择首个通过禁词和季节校验的变体。"""
    h = hashlib.sha1(seed_key.encode("utf-8"), usedforsecurity=False).digest()
    start = int.from_bytes(h[:4], "big") % len(variants)
    for offset in range(len(variants)):
        candidate = f"{phrase}　{variants[(start + offset) % len(variants)]}"
        if validate(candidate, season=season)[0]:
            return candidate
    # 所有词库候选都被未来规则禁用时仍保证邮件可发；该句无季节冲突和禁词。
    candidate = f"{phrase}　静水流深"
    if validate(candidate, season=season)[0]:
        return candidate
    raise ValueError(f"no valid static subject for phrase={phrase!r} season={season!r}")


def static_fallback(data: SubjectData) -> str:
    """
    根据信号 + 情绪 给出静态兜底主题。永远返回有效的 8 字主题。

    优先级(用户要求 DCA 不参与主题选择,因 DCA 在 14 只持仓中过于常见,
    会"垄断"主题让市场情绪主题词永远轮不到):
        LUMP-SUM > 极端情绪(极贪/极恐) > 偏热/偏冷 > 中性静默

    DCA 信号仅作为 prompt 语境传递给 LLM,不影响后 4 字选择。
    """
    phrase = data.solar_term.phrase
    sig = data.signals
    mood = data.mood.label
    season = season_of(data.solar_term.current)
    # seed 含节气起始日 + 进入第几天,确保同节气内每天 seed 不同 → 不同变体
    today_key = (
        f"{data.solar_term.current_date.isoformat()}-d{data.solar_term.days_into}"
    )

    # 1. LUMP-SUM(罕见且重要,保留为最高优先级)
    if sig.lump_sum_count > 0:
        return _pick_valid(
            phrase=phrase, variants=LUMP_SUM_VARIANTS,
            seed_key=today_key + "LUMP", season=season,
        )

    # 2-5. 完全按市场情绪选择,跳过 DCA
    if mood == "极度贪婪":
        return _pick_valid(
            phrase=phrase, variants=EXTREME_GREED_VARIANTS,
            seed_key=today_key + "GREED", season=season,
        )

    if mood == "极度恐慌":
        return _pick_valid(
            phrase=phrase, variants=EXTREME_FEAR_VARIANTS,
            seed_key=today_key + "FEAR", season=season,
        )

    if mood == "偏热":
        return _pick_valid(
            phrase=phrase, variants=WARM_VARIANTS,
            seed_key=today_key + "WARM", season=season,
        )

    if mood == "偏冷":
        return _pick_valid(
            phrase=phrase, variants=COLD_VARIANTS,
            seed_key=today_key + "COLD", season=season,
        )

    return _pick_valid(
        phrase=phrase, variants=NEUTRAL_VARIANTS,
        seed_key=today_key + "NEUT", season=season,
    )
