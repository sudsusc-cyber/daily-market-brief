"""
每日晨报主入口(M3 端到端,5 大模块原始数据)。

链路:
    config.HOLDINGS  →  collectors:
                          stocks / company_news / figures / macro_news
                          / buffett_13f / sentiment
                                    ↓
                              renderer/render(原始数据 dump,无 LLM)
                                    ↓
                              sender/smtp_sender.send_html_email
                                    (含 logo inline 附件)

时区:全程内部用 UTC,展示与邮件标题用北京时间。
本地运行:`uv run python -m src.main`
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from src.collectors import buffett_13f, company_news, figures, macro_news, sentiment, stocks
from src.config import HOLDINGS, Holding
from src.renderer.render import render_email
from src.sender.smtp_sender import InlineImage, send_html_email
from src.settings import load_settings
from src.utils.dates import now_beijing

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_LOGOS_DIR = _PROJECT_ROOT / "assets" / "logos"
_STATE_DIR = _PROJECT_ROOT / "state"


def _load_logo_assets(holdings: list[Holding]) -> tuple[dict[str, str], list[InlineImage]]:
    """扫描 assets/logos/<slug>.png,组装 (cid 映射, InlineImage 列表)"""
    cids: dict[str, str] = {}
    images: list[InlineImage] = []
    for h in holdings:
        path = _LOGOS_DIR / f"{h.slug}.png"
        if not path.exists():
            logger.warning("logo.missing ticker=%s expected=%s", h.ticker, path)
            continue
        cid = h.logo_cid
        cids[h.ticker] = cid
        images.append(InlineImage(cid=cid, path=path, subtype=None))  # 让 sender magic-bytes 推
    logger.info("logos.loaded count=%d/%d", len(cids), len(holdings))
    return cids, images


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    settings = load_settings()
    now_bj = now_beijing()

    logger.info("main.start  generated_at=%s", now_bj.isoformat(timespec="seconds"))

    # ---------- 数据采集 ----------
    logger.info("collect.stocks count=%d", len(HOLDINGS))
    signals = stocks.fetch_all(HOLDINGS)

    logger.info("collect.company_news")
    cn_bundles = company_news.fetch_all(HOLDINGS, settings.finnhub_api_key)

    logger.info("collect.macro_news")
    macro_bundles = macro_news.fetch_all()

    logger.info("collect.figures")
    fig_bundles = figures.fetch_all(state_path=_STATE_DIR / "pushed_figures.json")

    logger.info("collect.buffett_13f")
    buffett_bundle = buffett_13f.fetch(state_path=_STATE_DIR / "last_13f.json")

    logger.info("collect.sentiment")
    sentiment_bundle = sentiment.fetch_all(settings.fred_api_key)

    # ---------- 渲染 ----------
    logger.info("render")
    logo_cids, inline_images = _load_logo_assets(HOLDINGS)
    html = render_email(
        signals=signals,
        generated_at=now_bj,
        logo_cids=logo_cids,
        sentiment=sentiment_bundle,
        company_news=cn_bundles,
        figures=fig_bundles,
        macro_news=macro_bundles,
        buffett_13f=buffett_bundle,
    )

    # ---------- 发送 ----------
    subject = f"每日晨报 · {now_bj.year} 年 {now_bj.month} 月 {now_bj.day} 日"
    logger.info("send recipient=%s subject=%r", settings.email_recipient, subject)
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
