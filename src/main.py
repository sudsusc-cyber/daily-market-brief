"""
每日晨报主入口(M4 端到端,5 大模块原始数据 + LLM 加工)。

链路:
    config.HOLDINGS  →  collectors:
                          stocks / company_news / figures / macro_news
                          / buffett_13f / sentiment
                                    ↓
                          translator(标题英→中)
                                    ↓
                          processors:
                          news_summarizer / macro_filter
                          / figure_filter / sentiment_judge
                                    ↓
                          renderer/render(段落 + 原始列表 fallback)
                                    ↓
                          sender/smtp_sender.send_html_email
                                    (含 logo inline 附件)

时区:全程内部用 UTC,展示与邮件标题用北京时间。
任何 LLM 调用失败 → 降级到 M3 原始数据展示(模板已支持)。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from src.collectors import buffett_13f, company_news, figures, header_image, macro_news, sentiment, stocks
from src.config import HOLDINGS, Holding
from src.processors import (
    figure_filter,
    macro_filter,
    news_summarizer,
    sentiment_judge,
    translator,
)
from src.processors.llm_client import LLMClient
from src.renderer.render import render_email
from src.sender.smtp_sender import InlineImage, send_html_email
from src.settings import load_settings
from src.utils.dates import now_beijing

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_LOGOS_DIR = _PROJECT_ROOT / "assets" / "logos"
_STATE_DIR = _PROJECT_ROOT / "state"


def _translate_all_bundles(
    *,
    cn_bundles: list,
    fig_bundles: list,
    macro_bundles: list,
    client: LLMClient,
) -> None:
    """把所有 collector 的标题就地替换为中文,只翻译模板渲染的前 5 条"""
    titles_to_translate: list[object] = []
    for b in cn_bundles:
        titles_to_translate.extend(b.items[:5])
    for f in fig_bundles:
        titles_to_translate.extend(f.items[:5])
    for m in macro_bundles:
        titles_to_translate.extend(m.items[:5])
    if titles_to_translate:
        translator.translate_in_place_news(titles_to_translate, client=client)


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
        images.append(InlineImage(cid=cid, path=path, subtype=None))
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

    # ---------- 数据采集(M2 / M3) ----------
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

    # ---------- LLM 处理(M4) ----------
    llm = LLMClient(api_key=settings.deepseek_api_key)

    logger.info("translate.titles")
    _translate_all_bundles(
        cn_bundles=cn_bundles,
        fig_bundles=fig_bundles,
        macro_bundles=macro_bundles,
        client=llm,
    )

    logger.info("processors.news_summarizer")
    company_news_summary = news_summarizer.summarize(cn_bundles, client=llm)

    logger.info("processors.macro_filter")
    macro_news_summary = macro_filter.summarize(macro_bundles, client=llm)

    logger.info("processors.figure_filter")
    figure_summaries = figure_filter.filter_all(fig_bundles, client=llm)

    logger.info("processors.sentiment_judge")
    sentiment_verdict = sentiment_judge.judge(sentiment_bundle, client=llm)

    # token 成本汇总
    cum = llm.cumulative
    cost_cny = llm.estimate_cost_cny()
    logger.info(
        "llm.summary input=%d output=%d reasoning=%d cache_hit=%d  est_cost=¥%.4f",
        cum.input_tokens, cum.output_tokens, cum.reasoning_tokens, cum.cache_hit_tokens,
        cost_cny,
    )

    # ---------- 刊头图(M5) ----------
    logger.info("collect.header_image")
    header = header_image.pick_header_image(now_bj.date())

    # ---------- 渲染 ----------
    logger.info("render")
    logo_cids, inline_images = _load_logo_assets(HOLDINGS)
    if header["source"] == "local":
        inline_images.append(InlineImage(
            cid="header_fallback",
            path=_PROJECT_ROOT / "assets" / "fallback_header.jpg",
            subtype=None,
        ))
    html = render_email(
        signals=signals,
        generated_at=now_bj,
        logo_cids=logo_cids,
        header_image_url=header["url"],
        # 加工产物(为 None 时模板自动 fallback 到原始数据展示)
        sentiment=sentiment_bundle,
        sentiment_verdict=sentiment_verdict,
        company_news=cn_bundles,
        company_news_summary=company_news_summary,
        figures=fig_bundles,
        figure_summaries=figure_summaries,
        macro_news=macro_bundles,
        macro_news_summary=macro_news_summary,
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

    logger.info("main.done  est_cost=¥%.4f", cost_cny)
    return 0


if __name__ == "__main__":
    sys.exit(main())
