"""
一次性脚本:为所有持仓公司拉 logo 文件,保存到 assets/logos/<slug>.png。

数据源策略(2026-04 实测):
  1) Clearbit (logo.clearbit.com) — 2024 年起逐步停服,本脚本不再使用
  2) Google S2 favicon —— 稳定可达,返回 128x128 RGBA PNG。
     底层是公司 favicon 缩放,大公司 favicon 已是 192px+ 高清,效果可接受
  3) 若用户对某只 logo 不满,可手动覆盖到 assets/logos/<slug>.png

URL 模板(本脚本使用):
    https://www.google.com/s2/favicons?domain=<domain>&sz=128

后续 logo 更新或新增持仓后,重跑此脚本即可。
"""

from __future__ import annotations

import sys
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import HOLDINGS  # noqa: E402

LOGOS_DIR = PROJECT_ROOT / "assets" / "logos"
SIZE = 128
TIMEOUT = 20
UA = {"User-Agent": "daily-market-brief/0.1 (sudsusc@gmail.com)"}

# Google S2 favicon 对部分公司返回过小图(TSM / Costco / 旧 abc.xyz 在邮件里发糊)。
# 这里给特定 ticker 提供 Wikimedia Commons SVG 渲染的高清 PNG,优先级高于 Google S2。
# 用 commons.wikimedia.org/wiki/Special:FilePath/<File>?width=N,自动 302 到 SVG→PNG 缩略图,
# 比硬编码 upload.wikimedia.org 的 hash 路径稳。
# 维护:若官方 logo 改名,在 commons.wikimedia.org 搜文件名替换即可。
def _wiki(file_name: str, width: int = 512) -> str:
    """Wikimedia Commons 文件 → 自动 302 到指定宽度的 PNG"""
    from urllib.parse import quote
    return f"https://commons.wikimedia.org/wiki/Special:FilePath/{quote(file_name)}?width={width}"


def _fmp(ticker: str) -> str:
    """Financial Modeling Prep 公开 logo CDN,以 ticker 为 key,128x128 方形 PNG"""
    return f"https://financialmodelingprep.com/image-stock/{ticker}.png"


LOGO_OVERRIDES: dict[str, str] = {
    # 移动端某些邮件客户端对非方形 inline 图片渲染不一致(M2 验收发现)。
    # 因此凡是用 override 的,都用 **方形** 来源(FMP CDN 默认 250x250 / 128x128)。
    # Wikimedia 的 SVG 渲染常出现长宽比不一致(TSMC 文字版 960x757),已弃用。
    "TSM": _fmp("TSM"),     # 250x250 方形,替换 Wikimedia 的 960x757 文字版
    "COST": _fmp("COST"),   # Wikipedia 的 Costco 是 960x344 长条形,FMP 是方形
    "BRK.B": _fmp("BRK.B"), # Berkshire 官网无 favicon,FMP 有 8.5KB 方形 logo
}


def fetch_one(domain: str, dest: Path, override_url: str | None = None) -> tuple[bool, int, str]:
    """返回 (成功?, 字节数, 备注)"""
    url = override_url or f"https://www.google.com/s2/favicons?domain={domain}&sz={SIZE}"
    try:
        resp = requests.get(url, headers=UA, timeout=TIMEOUT, allow_redirects=True)
    except Exception as exc:  # noqa: BLE001
        return False, 0, f"网络异常: {exc}"
    if resp.status_code != 200:
        return False, 0, f"HTTP {resp.status_code}"
    if not resp.content or len(resp.content) < 100:
        return False, len(resp.content), f"响应过小({len(resp.content)} bytes),可能是 1x1 占位"
    dest.write_bytes(resp.content)
    return True, len(resp.content), "ok"


def main() -> int:
    LOGOS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"目标目录: {LOGOS_DIR.relative_to(PROJECT_ROOT)}")
    print(f"共 {len(HOLDINGS)} 个 logo,size={SIZE}\n")

    from urllib.parse import urlparse

    failed: list[tuple[str, str]] = []
    total_bytes = 0
    for h in HOLDINGS:
        dest = LOGOS_DIR / f"{h.slug}.png"
        override = LOGO_OVERRIDES.get(h.ticker)
        ok, n, msg = fetch_one(h.logo_domain, dest, override_url=override)
        total_bytes += n
        icon = "✅" if ok else "❌"
        source = urlparse(override).hostname.replace("www.", "") if override else h.logo_domain
        print(f"  {icon} {h.ticker:<10} ← {source:<32} {n:>7} bytes  {msg}")
        if not ok:
            failed.append((h.ticker, msg))

    print(f"\n合计 {total_bytes:,} bytes")
    if failed:
        print(f"\n失败 {len(failed)} 个:")
        for t, m in failed:
            print(f"  - {t}: {m}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
