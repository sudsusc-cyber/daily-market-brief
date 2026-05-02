"""一次性脚本:用 mock 数据渲染完整邮件(头图 + 持仓表格 + logo),
发到 TEST_RECIPIENT(或 EMAIL_RECIPIENT 第一个地址),验证 Android QQ 邮箱里:
  - 头图能否显示(inline CID 修复)
  - 持仓表格里 ticker / 公司名是否被压成一字一行(white-space:nowrap 修复)

不调 LLM、不收新闻,纯本地渲染。
不影响 idempotency 状态。

用法(从 worktree 跑):
    export TEST_RECIPIENT=xxx@qq.com   # 必须,避免把私人收件人写进公开脚本
    uv run python scripts/send_test_email.py
"""

from __future__ import annotations

import hashlib
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# ─────────────────────────  路径与 env  ───────────────────────────
_HERE = Path(__file__).resolve()
_WORKTREE_ROOT = _HERE.parent.parent
_ENV_FILE = _WORKTREE_ROOT / ".env"
if not _ENV_FILE.exists():
    sys.exit(f"找不到 .env: {_ENV_FILE}（请在项目根 {_WORKTREE_ROOT} 创建 .env）")

for line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, _, v = line.partition("=")
    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

sys.path.insert(0, str(_WORKTREE_ROOT))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

from src.collectors.header_image import pick_header_image  # noqa: E402
from src.collectors.stocks import StockSignal  # noqa: E402
from src.config import HOLDINGS, Holding  # noqa: E402
from src.renderer.render import render_email  # noqa: E402
from src.sender.smtp_sender import InlineImage, send_html_email  # noqa: E402

_LOGOS_DIR = _WORKTREE_ROOT / "assets" / "logos"


def _build_mock_signals() -> list[StockSignal]:
    """构造 12 只假 signal,覆盖 NONE / DCA / LUMP_SUM 三种状态。"""
    cases: list[tuple[Holding, float | None, float | None, float | None, str | None]] = [
        (HOLDINGS[0], 414.44, 438.85, 381.78, None),   # MSFT, NONE
        (HOLDINGS[1], 1011.70, 908.45, 756.63, None),  # COST, NONE
        (HOLDINGS[2], 280.14, 226.43, 202.18, None),   # AAPL, NONE
        (HOLDINGS[3], 198.45, 140.20, 96.16, None),    # NVDA, NONE
        (HOLDINGS[4], 397.67, 219.97, 167.42, None),   # TSM, NONE
        (HOLDINGS[5], 478.30, 502.80, 390.50, None),   # MCO, DCA
        (HOLDINGS[6], 198.40, 185.60, 152.30, None),   # GOOG, NONE
        (HOLDINGS[7], 462.10, 451.20, 388.40, None),   # BRK.B, NONE(无 logo)
        (HOLDINGS[8], 64.20, 68.40, 60.10, None),      # KO, DCA
        (HOLDINGS[9], 268.90, 295.40, 312.80, None),   # AXP, LUMP_SUM
        (HOLDINGS[10], 412.40, 380.60, 340.20, None),  # 0700.HK, NONE
        (HOLDINGS[11], 157.10, 136.01, 89.67, None),   # 9992.HK 泡泡玛特, NONE
    ]
    signals: list[StockSignal] = []
    for h, last, s120, s200, err in cases:
        if err:
            signals.append(StockSignal(h, None, None, None, None, None, "NONE", error=err))
            continue
        assert last and s120 and s200
        d120 = (last - s120) / s120
        d200 = (last - s200) / s200
        sig = "LUMP_SUM" if last <= s200 else ("DCA" if last <= s120 else "NONE")
        signals.append(StockSignal(h, last, s120, s200, d120, d200, sig))
    return signals


def _logo_path(h: Holding) -> Path | None:
    for ext in ("png", "jpg", "jpeg"):
        p = _LOGOS_DIR / f"{h.slug}.{ext}"
        if p.exists():
            return p
    return None


def _build_logos() -> tuple[dict[str, str], list[InlineImage]]:
    """ticker -> CID(同 main.py 风格:logo_<slug>_<sha8>),并构造 InlineImage 列表"""
    cids: dict[str, str] = {}
    images: list[InlineImage] = []
    for h in HOLDINGS:
        p = _logo_path(h)
        if p is None:
            continue
        sha8 = hashlib.sha1(p.read_bytes()).hexdigest()[:8]
        cid = f"{h.logo_cid}_{sha8}"
        cids[h.ticker] = cid
        images.append(InlineImage(cid=cid, path=p, subtype=None))
    return cids, images


def main() -> int:
    sender = os.environ["QQ_EMAIL_ADDRESS"]
    auth_code = os.environ["QQ_EMAIL_AUTH_CODE"]
    recipient = (
        os.environ.get("TEST_RECIPIENT")
        or (os.environ.get("EMAIL_RECIPIENT", "").split(",")[0].strip() or None)
    )
    if not recipient:
        sys.exit("请设置 TEST_RECIPIENT 或 EMAIL_RECIPIENT 环境变量")

    now_bj = datetime.now(ZoneInfo("Asia/Shanghai"))

    logger.info("pick_header_image")
    header = pick_header_image(now_bj.date())
    logger.info("header source=%s id=%s path=%s",
                header["source"], header["id"], header["local_path"])

    logger.info("build mock signals & logos")
    signals = _build_mock_signals()
    logo_cids, logo_images = _build_logos()
    logger.info("logos count=%d/%d", len(logo_cids), len(HOLDINGS))

    inline_images = list(logo_images)
    inline_images.append(InlineImage(
        cid="header_image",
        path=Path(header["local_path"]),
        subtype=None,
    ))

    holdings_intro = (
        "潮水退去,礁石毕现,但我们关心的是潮水之外的那块岩石——它在低处安静堆放,"
        "你须弯腰才能拾起。市场如海,此刻退潮显露的礁石,正是耐心者早已凝视的方向。"
    )

    logger.info("render email")
    html = render_email(
        signals=signals,
        generated_at=now_bj,
        logo_cids=logo_cids,
        header_image_url=header["url"],   # cid:header_image
        holdings_intro=holdings_intro,
        # 故意不传 sentiment / news / 13F:模板会优雅省略,只渲染头图 + 持仓信号 + footer
    )

    # 调试:先把 HTML 写到 /tmp 备查
    debug_path = Path("/tmp/send_test_email.html")
    debug_path.write_text(html, encoding="utf-8")
    logger.info("debug html bytes=%d path=%s", len(html.encode("utf-8")), debug_path)

    subject = f"【完整测试】持仓表 + 头图 · {now_bj.strftime('%Y-%m-%d %H:%M')}"
    logger.info("send to %s subject=%r inline_images=%d", recipient, subject, len(inline_images))
    send_html_email(
        sender=sender,
        sender_display_name="渲染测试",
        auth_code=auth_code,
        recipient=recipient,
        subject=subject,
        html_body=html,
        inline_images=inline_images,
    )
    logger.info("done. 请在 Android QQ 邮箱查收并确认:")
    logger.info("  1. 头图正常显示")
    logger.info("  2. 持仓表 ticker(MSFT/AAPL/NVDA…)横排,不再一字一行")
    logger.info("  3. 公司名(Microsoft/Apple…)横排")
    return 0


if __name__ == "__main__":
    sys.exit(main())
