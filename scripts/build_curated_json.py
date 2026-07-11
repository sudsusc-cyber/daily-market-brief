"""读 state/curation/curate_result.json，生成 200 张 schema v2 JSON。"""
from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CURATION_DIR = ROOT / "state" / "curation"
CURATE_RESULT = CURATION_DIR / "curate_result.json"
DROPPED_RESULT = CURATION_DIR / "dropped.json"

# Agent 审美淘汰列表
DROPS = {
    # spring
    "1226302": "特写微距,不适合横幅",
    "149521": "花卉特写,偏装饰性",
    "4500037": "季节归属错,夏景",
    "4468715": "季节归属错,夏景",
    "10770234": "剪影+蓝天,接近励志风",
    "795622": "过于平淡通用,缺记忆点",
    "4497588": "晴朗鲜绿,饱和偏高",
    # summer
    "240040": "标题极泛,通用图库感",
    "1822996": "电线杂乱,破坏克制感",
    # autumn
    "6435268": "宫殿景点,商业图库感",
    "34063300": "金黄+鲜蓝天,饱和过高",
    "17399019": "鲜蓝天,色调不符",
}


def infer_palette(slug: str) -> str:
    """从 slug 推断主色调"""
    s = slug.lower()
    if "monochrome" in s or "black-and-white" in s or "grayscale" in s:
        return "monochrome"
    if "snow" in s or "frozen" in s or "winter" in s and "snow" in s:
        return "cool_white"
    if "moss" in s or "green-pine" in s or "coniferous" in s or "green-forest" in s:
        return "muted_green"
    if "ocean" in s or "sea" in s or "coastal" in s or "fjord" in s or "blue-body" in s:
        return "cool_blue"
    if "autumn" in s or "birch" in s or "dry-grass" in s or "wheat" in s or "brown" in s or "dried" in s:
        return "warm_grey"
    if "moody" in s or "dark" in s or "storm" in s or "twilight" in s or "heavy-clouds" in s:
        return "dark_grey"
    if "fog" in s or "misty" in s or "mist" in s or "haze" in s or "overcast" in s or "cloudy" in s:
        return "cool_grey"
    return "neutral_grey"


def infer_tags(slug: str) -> list[str]:
    """从 slug 提取关键 tags(去停用词后取 3-5 个)"""
    stop = {
        "a", "an", "the", "of", "on", "in", "at", "with", "and", "by", "for",
        "photo", "photography", "scene", "view", "image", "background",
        "landscape", "free", "stock", "during", "from", "near", "between", "under", "over",
        "is", "are", "be", "to",
    }
    tokens = re.split(r"[-_]", slug.lower())
    out = []
    for t in tokens:
        if t in stop or len(t) < 3:
            continue
        if t.isdigit():
            continue
        out.append(t)
    return out[:5]


def main() -> int:
    with CURATE_RESULT.open(encoding="utf-8") as f:
        data = json.load(f)
    valid = data["valid"]

    by_season: dict[str, list[dict]] = {"spring": [], "summer": [], "autumn": [], "winter": []}
    dropped: list[dict] = []
    for v in valid:
        if v["id"] in DROPS:
            dropped.append({**v, "drop_reason": DROPS[v["id"]]})
            continue
        entry = {
            "id": v["id"],
            "slug": v["slug"],
            "credit": "Photo on Pexels",  # 用户审阅时可指定补摄影师姓名
            "tags": infer_tags(v["slug"]),
            "palette": infer_palette(v["slug"]),
        }
        by_season[v["season"]].append(entry)

    # 校验每季 50
    for s, items in by_season.items():
        print(f"{s}: {len(items)}")
        if len(items) != 50:
            raise ValueError(f"{s} != 50: {len(items)}")

    out = {
        "version": 2,
        "updated_at": str(date.today()),
        "policy": "northern_hemisphere_meteorological",
        "source": "pexels",
        "url_template": "https://images.pexels.com/photos/{id}/pexels-photo-{id}.jpeg?auto=compress&cs=tinysrgb&w=1280&h=400&fit=crop",
        "preview_template": "https://www.pexels.com/photo/{slug}-{id}/",
        "seasons": by_season,
    }
    target = ROOT / "config" / "curated_images.json"
    target.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"\nwrote {target} ({target.stat().st_size} bytes, {sum(len(v) for v in by_season.values())} entries)")

    # 统计 palette 分布
    from collections import Counter
    for s, items in by_season.items():
        pc = Counter(i["palette"] for i in items)
        print(f"  {s} palettes: {dict(pc)}")

    # dropped report
    print(f"\ndropped: {len(dropped)}")
    CURATION_DIR.mkdir(parents=True, exist_ok=True)
    DROPPED_RESULT.write_text(
        json.dumps(dropped, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
