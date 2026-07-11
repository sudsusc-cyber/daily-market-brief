"""读配置与 state/curation/dropped.json，生成图片审阅文档。"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CFG = ROOT / "config" / "curated_images.json"
DROPPED = ROOT / "state" / "curation" / "dropped.json"
OUT = ROOT / "docs" / "curated_images_review.md"

SEASON_LABELS = {"spring": "春", "summer": "夏", "autumn": "秋", "winter": "冬"}
SEASON_MONTHS = {"spring": "3-5 月", "summer": "6-8 月", "autumn": "9-11 月", "winter": "12-2 月"}


def main() -> int:
    cfg = json.loads(CFG.read_text())
    dropped = json.loads(DROPPED.read_text())

    lines: list[str] = []
    a = lines.append

    a("# 候选图审阅清单 — M5.4 第一交付物\n")
    a(f"- 版本:{cfg['version']}")
    a(f"- 更新日期:{cfg['updated_at']}")
    a(f"- 图源:**{cfg['source']}**(原 SPEC 写 Unsplash,因主站反爬不可达切到 Pexels,详 ADR-0009)")
    a(f"- 季节策略:{cfg['policy']}(春 3-5 / 夏 6-8 / 秋 9-11 / 冬 12-2)")
    a("- 总计:**200 张** = 4 季 × 50 张(严格)")
    a(f"- CDN URL 模板:`{cfg['url_template']}`")
    a(f"- 预览页模板:`{cfg['preview_template']}`")
    a("")
    a("## 工作流说明\n")
    a("1. **CC** 用 WebSearch 搜 `site:pexels.com/photo` 共 46 组关键词,收集 283 张原始候选 ID")
    a("2. **CC** 元数据粗筛(BLOCKLIST 含 sunset/sunrise/woman/man/colorful/golden/neon 等 50+ 词)→ 230 张通过")
    a("3. **CC** 并发 HEAD 验证 CDN URL 200 OK + Content-Length → 212 张可达(18 张真 404)")
    a("4. **审美 Agent**(模拟 frontend-design 风格审美)按 SPEC M5.2 克制标准最后过滤 → 砍 12 张 → **200 张**")
    a("")
    a("## 你要做的事\n")
    a("- 在每张表格的 **预览** 列点链接打开,扫一眼图")
    a("- **不喜欢**的:在 **决定** 列填 `❌`")
    a("- **想换**(同主题但要求别的):填 `🔄 + 描述`(例:`🔄 想要更冷调的、不要这种近景`)")
    a('- 全部审完发我:`已删除第 X、Y、Z 张,补 N 张同类`,我重新搜 + 验证 + 重新提交此文档')
    a('- **直到你说"全部 OK,可以集成"**,我才开始 M5.5(模板改 / collector 写 / fallback_header.jpg 选)')
    a("")
    a("## 字段说明\n")
    a("- **Photo ID**:Pexels 数字 ID,即将填入 `config/curated_images.json` `seasons.{season}[].id`")
    a('- **主题 / Slug**:Pexels 自动生成的英文 slug(由 alt 文字派生),所以"主题准确度"以你点开预览为准 — slug 不一定描述准确')
    a("- **调色板**:CC 基于 slug 推断的 palette label(`cool_grey` / `cool_white` / `warm_grey` / `cool_blue` / `muted_green` / `dark_grey` / `monochrome` / `neutral_grey`),仅供你筛选时按色调浏览参考")
    a("- **预览**:点开看图本身(`unsplash.com/photos/...` 等价的 Pexels 详情页)")
    a("")

    for season in ("spring", "summer", "autumn", "winter"):
        items = cfg["seasons"][season]
        a("\n---\n")
        a(f"## {SEASON_LABELS[season]}({SEASON_MONTHS[season]},{len(items)} 张)\n")
        a("| # | Photo ID | 主题 / Slug | 标签 | 调色板 | 预览 | 决定 |")
        a("|---|---|---|---|---|---|---|")
        for i, x in enumerate(items, 1):
            preview = f"https://www.pexels.com/photo/{x['slug']}-{x['id']}/"
            tags = ", ".join(x["tags"])
            a(f"| {i} | `{x['id']}` | {x['slug']} | {tags} | `{x['palette']}` | [预览]({preview}) |  |")

    a("\n---\n")
    a("## 已被审美 Agent 淘汰的 12 张(供你交叉校验)\n")
    a("| Photo ID | 季节 | Slug | 淘汰理由 |")
    a("|---|---|---|---|")
    for d in dropped:
        a(f"| `{d['id']}` | {SEASON_LABELS[d['season']]} | {d['slug']} | {d['drop_reason']} |")
    a("")
    a("> 如果你认为其中任何一张应该**保留**,告诉我,我把它放回对应季节,然后从该季淘汰另一张(由你指定)。\n")

    a("\n---\n")
    a('## 调色板分布(供你"按色挑"参考)\n')
    from collections import Counter
    for season in ("spring", "summer", "autumn", "winter"):
        pc = Counter(x["palette"] for x in cfg["seasons"][season])
        line = ", ".join(f"{k}={v}" for k, v in pc.most_common())
        a(f"- **{SEASON_LABELS[season]}**:{line}")
    a("")

    a("\n---\n")
    a("## 下一步(用户审阅通过后)\n")
    a("1. CC 从用户审阅通过的春 / 冬池里挑 1 张最稳妥的(雾山 / 雪原 / 留白海面),压缩到 1280×400 + JPEG q=80 + ≤80KB,commit 为 `assets/fallback_header.jpg`")
    a("2. CC 实现 `src/collectors/header_image.py`(三层降级:Pexels 白名单 → Bing 每日 → 本地 fallback),5s 超时,不重试,永不抛异常")
    a("3. CC 改 `src/main.py` 调用 + `src/renderer/templates/email.html.j2` 顶部加刊头图区(**铁律:不动 M4 已定稿正文**)")
    a("4. CC 写 `tests/test_header_image.py`(覆盖 200 / 主源失败 / Bing 失败 / 文件丢失等场景)")
    a("5. CC 写 ADR `docs/decisions/0009-email-rendering-slo.md`(SPEC M5.9 要求,编号顺延)")
    a("6. CC 跨客户端兼容性测试(QQ webmail / QQ mac / Gmail / Apple Mail / Outlook)")

    OUT.write_text("\n".join(lines))
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")
    print(f"  total entries documented: {sum(len(cfg['seasons'][s]) for s in cfg['seasons'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
