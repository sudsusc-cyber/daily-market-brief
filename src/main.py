"""
每日晨报主入口(M2 端到端最小流程)。

链路:
    config.HOLDINGS  →  collectors/stocks.fetch_all  →  renderer/render
                                                          ↓
                                              sender/smtp_sender.send_html_email

时区:全程内部用 UTC,展示与邮件标题用北京时间。
本地运行:`uv run python -m src.main`
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from src.collectors.stocks import fetch_all
from src.config import HOLDINGS
from src.renderer.render import render_email
from src.sender.smtp_sender import send_html_email
from src.settings import load_settings

logger = logging.getLogger(__name__)

_BEIJING = ZoneInfo("Asia/Shanghai")


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

    logger.info("main.render")
    html = render_email(signals=signals, generated_at=now_bj)

    subject = f"每日晨报 · {now_bj.year} 年 {now_bj.month} 月 {now_bj.day} 日"
    logger.info("main.send  recipient=%s subject=%r", settings.email_recipient, subject)
    send_html_email(
        sender=settings.qq_email_address,
        auth_code=settings.qq_email_auth_code,
        recipient=settings.email_recipient,
        subject=subject,
        html_body=html,
    )

    logger.info("main.done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
