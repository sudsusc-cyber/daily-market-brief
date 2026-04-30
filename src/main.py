"""
每日晨报主入口(M2 端到端最小流程)。

链路:
    config.HOLDINGS  →  collectors/stocks.fetch_all  →  renderer/render
                                                          ↓
                                              sender/smtp_sender.send_html_email
                                                  (含 logo inline 附件)

时区:全程内部用 UTC,展示与邮件标题用北京时间。
本地运行:`uv run python -m src.main`
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from src.collectors.stocks import fetch_all
from src.config import HOLDINGS, Holding
from src.renderer.render import render_email
from src.sender.smtp_sender import InlineImage, send_html_email
from src.settings import load_settings

logger = logging.getLogger(__name__)

_BEIJING = ZoneInfo("Asia/Shanghai")
_LOGOS_DIR = Path(__file__).resolve().parent.parent / "assets" / "logos"


def _load_logo_assets(holdings: list[Holding]) -> tuple[dict[str, str], list[InlineImage]]:
    """
    扫描 assets/logos/<slug>.png,组装两份产物:
      - cids:   ticker -> CID 字符串(给模板)
      - images: list[InlineImage](给 SMTP 嵌入)
    缺失的 ticker(如 BRK.B 没拉到)会跳过,模板里走文字 fallback。
    """
    cids: dict[str, str] = {}
    images: list[InlineImage] = []
    for h in holdings:
        path = _LOGOS_DIR / f"{h.slug}.png"
        if not path.exists():
            logger.warning("logo.missing ticker=%s expected=%s", h.ticker, path)
            continue
        cid = h.logo_cid
        cids[h.ticker] = cid
        images.append(InlineImage(cid=cid, path=path, subtype="png"))
    logger.info("logos.loaded count=%d/%d", len(cids), len(holdings))
    return cids, images


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    settings = load_settings()
    now_bj = datetime.now(_BEIJING)

    logger.info("main.start  generated_at=%s", now_bj.isoformat(timespec="seconds"))

    logger.info("main.collect.stocks  count=%d", len(HOLDINGS))
    signals = fetch_all(HOLDINGS)
    ok = sum(1 for s in signals if s.error is None)
    logger.info("main.collect.stocks.done  ok=%d/%d", ok, len(signals))

    logger.info("main.load_logos")
    logo_cids, inline_images = _load_logo_assets(HOLDINGS)

    logger.info("main.render")
    html = render_email(signals=signals, generated_at=now_bj, logo_cids=logo_cids)

    subject = f"每日晨报 · {now_bj.year} 年 {now_bj.month} 月 {now_bj.day} 日"
    logger.info("main.send  recipient=%s subject=%r", settings.email_recipient, subject)
    send_html_email(
        sender=settings.qq_email_address,
        auth_code=settings.qq_email_auth_code,
        recipient=settings.email_recipient,
        subject=subject,
        html_body=html,
        inline_images=inline_images,
    )

    logger.info("main.done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
