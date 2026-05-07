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
import os
import sys
from pathlib import Path

from src.collectors import (
    buffett_13f,
    company_news,
    figures,
    frontier_labs,
    header_image,
    macro_news,
    sentiment,
    stocks,
)
from src.config import HOLDINGS, Holding
from src.processors import (
    figure_filter,
    frontier_labs_filter,
    holdings_intro,
    macro_filter,
    news_summarizer,
    sentiment_judge,
    translator,
)
from src.processors.llm_client import LLMClient, resolve_latest_flash_model
from src.processors.thesis import extractor as thesis_extractor
from src.processors.thesis import renderer as thesis_renderer
from src.processors.thesis import rules as thesis_rules
from src.processors.thesis import state as thesis_state
from src.renderer.render import render_email
from src.sender.smtp_sender import InlineImage, send_html_email
from src.settings import load_settings
from src.utils.dates import now_beijing
from src.utils.holidays import should_send_today
from src.utils.idempotency import already_sent_today

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_LOGOS_DIR = _PROJECT_ROOT / "assets" / "logos"
_STATE_DIR = _PROJECT_ROOT / "state"

_ACTIVE_THESIS_STATUS_RANK = {
    "core": 0,
    "emerging": 1,
    "stable": 2,
    "dormant": 3,
}
_ACTIVE_THESIS_THEME_LIMIT = 120


def _select_active_thesis_themes(
    state_dict: dict,
    *,
    limit: int = _ACTIVE_THESIS_THEME_LIMIT,
) -> list[str]:
    """按状态重要性与最近 evidence 时间挑选注入 prompt 的 active themes。"""
    rows: list[tuple[int, str, str]] = []
    for theme, st in state_dict.items():
        status = getattr(st, "status", "")
        if status not in _ACTIVE_THESIS_STATUS_RANK:
            continue
        rows.append((
            _ACTIVE_THESIS_STATUS_RANK[status],
            getattr(st, "last_evidence_date", "") or "",
            str(theme),
        ))

    # 稳定排序：先 theme 字母序，再按最近 evidence 时间降序，最后按状态优先级。
    rows.sort(key=lambda row: row[2])
    rows.sort(key=lambda row: row[1], reverse=True)
    rows.sort(key=lambda row: row[0])
    return [theme for _, _, theme in rows[:limit]]


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
    """扫描 assets/logos/<slug>.{png,jpg,jpeg};按优先级取首个存在的文件。

    CID 形如 logo_<slug>_<sha8>:文件内容变 → hash 变 → CID 变,
    破解某些邮件客户端(如 iOS 微信邮件助手)对同名 CID 旧附件的缓存。
    """
    import hashlib
    cids: dict[str, str] = {}
    images: list[InlineImage] = []
    for h in holdings:
        path = next(
            (p for ext in ("png", "jpg", "jpeg")
             if (p := _LOGOS_DIR / f"{h.slug}.{ext}").exists()),
            None,
        )
        if path is None:
            logger.warning("logo.missing ticker=%s expected=%s/{png,jpg}", h.ticker, _LOGOS_DIR / h.slug)
            continue
        sha8 = hashlib.sha1(path.read_bytes()).hexdigest()[:8]
        cid = f"{h.logo_cid}_{sha8}"
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

    force_send = os.environ.get("FORCE_SEND", "").strip().lower() in ("1", "true")

    # ---------- 节假日预检(M6;cron 仍按周二-周六触发,但美股节假日要跳过) ----------
    if not force_send:
        ok, reason = should_send_today(now_bj.date())
        logger.info("holidays.check ok=%s reason=%s", ok, reason)
        if not ok:
            logger.info("main.skipped reason=%s", reason)
            return 0

    # ---------- 幂等性预检(双 cron 触发时,后触发的若发现今天已发过 → 跳过) ----------
    if not force_send and already_sent_today():
        logger.info("main.skipped reason=今日已通过另一次 cron 成功发送,跳过双触发")
        return 0

    # ---------- 数据采集(M2 / M3) ----------
    logger.info("collect.stocks count=%d", len(HOLDINGS))
    signals = stocks.fetch_all(HOLDINGS)

    logger.info("collect.company_news")
    company_news_state_path = _STATE_DIR / "pushed_company_news.json"
    cn_bundles, company_news_pending_pushed = company_news.fetch_all(
        HOLDINGS, settings.finnhub_api_key, state_path=company_news_state_path,
    )

    logger.info("collect.macro_news")
    macro_news_state_path = _STATE_DIR / "pushed_macro_news.json"
    macro_bundles, macro_news_pending_pushed = macro_news.fetch_all(state_path=macro_news_state_path)

    logger.info("collect.figures")
    figures_state_path = _STATE_DIR / "pushed_figures.json"
    fig_bundles, figures_pending_pushed = figures.fetch_all(state_path=figures_state_path)

    logger.info("collect.frontier_labs")
    frontier_labs_state_path = _STATE_DIR / "pushed_frontier_labs.json"
    frontier_labs_bundles, frontier_labs_pending_pushed = frontier_labs.fetch_all(
        state_path=frontier_labs_state_path,
    )

    logger.info("collect.buffett_13f")
    buffett_13f_state_path = _STATE_DIR / "last_13f.json"
    buffett_bundle, buffett_13f_pending_save = buffett_13f.fetch(state_path=buffett_13f_state_path)

    logger.info("collect.sentiment")
    sentiment_bundle = sentiment.fetch_all(settings.fred_api_key)

    # ---------- LLM 处理(M4) ----------
    deepseek_model = resolve_latest_flash_model(settings.deepseek_api_key)
    llm = LLMClient(api_key=settings.deepseek_api_key, model=deepseek_model)

    logger.info("translate.titles")
    _translate_all_bundles(
        cn_bundles=cn_bundles,
        fig_bundles=fig_bundles,
        macro_bundles=macro_bundles,
        client=llm,
    )

    logger.info("processors.news_summarizer")
    company_news_silence_note = None
    if cn_bundles:
        company_news_summary = news_summarizer.summarize(cn_bundles, client=llm)
    else:
        company_news_summary = None
        company_news_silence_note = news_summarizer.generate_silence_note(client=llm)

    logger.info("processors.macro_filter")
    macro_news_silence_note = None
    if macro_bundles:
        macro_news_summary = macro_filter.summarize(macro_bundles, client=llm)
    else:
        macro_news_summary = None
        macro_news_silence_note = macro_filter.generate_silence_note(client=llm)

    logger.info("processors.figure_filter")
    figure_summaries = figure_filter.filter_all(fig_bundles, client=llm)
    # M5.10 质量门槛:无 items 的人物(规则层全砍 / LLM 全 no)整个不渲染
    _before = len(figure_summaries)
    figure_summaries = [f for f in figure_summaries if f.items]
    if len(figure_summaries) < _before:
        logger.info("figure_filter.dropped_silent count=%d", _before - len(figure_summaries))
    # 全员沉默时:LLM 写一句古典韵味的占位语
    figure_silence_note = None
    figure_footnotes = []
    if not figure_summaries:
        figure_silence_note = figure_filter.generate_silence_note(llm)
    else:
        # 版面限流:质量评分后最多展示 3 位人物
        figure_summaries = figure_filter.select_voice_summaries(figure_summaries)
        # 跨人物统一编号 [1] [2] ...,章节底部一次列出所有来源
        figure_footnotes = figure_filter.assign_footnotes(figure_summaries)

    logger.info("processors.sentiment_judge")
    sentiment_verdict = sentiment_judge.judge(sentiment_bundle, client=llm)

    logger.info("processors.frontier_labs_filter")
    frontier_labs_items = frontier_labs_filter.filter_all(frontier_labs_bundles, client=llm)

    logger.info("processors.thesis")
    try:
        # 第一次读 state：获取 active themes 用于注入 prompt（防 theme 漂移）
        state_dict = thesis_state.load_state(_STATE_DIR)
        active_themes = _select_active_thesis_themes(state_dict)

        evidence_today = thesis_extractor.extract(
            client=llm,
            company_news=company_news_summary,
            macro_news=macro_news_summary,
            figure_summaries=figure_summaries,
            berkshire_events=buffett_bundle,
            frontier_labs_events=frontier_labs_items,
            active_themes=active_themes,
            today=now_bj.date(),
        )
        # extractor 只 return；写入由 state.py 统一负责（内部按 evidence_id 去重）
        thesis_state.append_evidence(evidence_today, _STATE_DIR)

        recent_evidence = thesis_state.load_recent_evidence(
            _STATE_DIR, days=90, today=now_bj.date(),
        )
        holdings_tickers = [h.ticker for h in HOLDINGS]
        state_dict, thesis_events = thesis_rules.run_state_transitions(
            today=now_bj.date(),
            state=state_dict,
            recent_evidence=recent_evidence,
            holdings_tickers=holdings_tickers,
        )

        # 更新 rolling_evidence
        by_theme_today: dict[str, list] = {}
        for e in evidence_today:
            by_theme_today.setdefault(e.theme, []).append(e)
        for theme, st in state_dict.items():
            if theme in by_theme_today:
                thesis_state.update_rolling_evidence(st, by_theme_today[theme])

        thesis_state.save_state(state_dict, _STATE_DIR)

        judgment_section = thesis_renderer.build_judgment_section(thesis_events)
    except Exception as exc:  # noqa: BLE001 — 任何 thesis 步骤失败都降级到无 judgment_section
        # 与项目其他异常处理对齐:不用 exc_info=True / logger.exception,因 OpenAI SDK 异常
        # traceback 可能带请求 url 或 header 痕迹;只记 type + 截断后的 str。
        logger.warning(
            "thesis.pipeline_failed exc_type=%s msg=%s",
            type(exc).__name__, str(exc)[:200],
        )
        judgment_section = None

    logger.info("processors.holdings_intro")
    holdings_intro_text = holdings_intro.write_intro(signals, client=llm)

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
    # 刊头图统一走 inline CID(Android QQ 邮箱不会自动加载远程图,iOS/桌面正常)。
    # header_image.pick_header_image 三层都会下载到本地并返回 local_path。
    inline_images.append(InlineImage(
        cid="header_image",
        path=header["local_path"],
        subtype=None,
    ))
    html = render_email(
        signals=signals,
        generated_at=now_bj,
        logo_cids=logo_cids,
        header_image_url=header["url"],
        holdings_intro=holdings_intro_text,
        # 加工产物(为 None 时模板自动 fallback 到原始数据展示)
        sentiment=sentiment_bundle,
        sentiment_verdict=sentiment_verdict,
        company_news=cn_bundles,
        company_news_summary=company_news_summary,
        figures=fig_bundles,
        figure_summaries=figure_summaries,
        figure_silence_note=figure_silence_note,
        figure_footnotes=figure_footnotes,
        macro_news=macro_bundles,
        macro_news_summary=macro_news_summary,
        company_news_silence_note=company_news_silence_note,
        macro_news_silence_note=macro_news_silence_note,
        buffett_13f=buffett_bundle,
        frontier_labs_items=frontier_labs_items,
        judgment_section=judgment_section,
    )

    # ---------- 主题生成(M5.11:DeepSeek 8 字两段四言古典对仗) ----------
    from src.processors.subject.extractor import extract_subject_data
    from src.processors.subject.generator import generate_subject
    subject_data = extract_subject_data(
        today_bj=now_bj.date(),
        signals=signals,
        sentiment_verdict=sentiment_verdict,
        sentiment_bundle=sentiment_bundle,
        company_news_summary=company_news_summary,
        macro_news_summary=macro_news_summary,
        email_html=html,
    )
    subject = generate_subject(subject_data, llm=llm, today_bj=now_bj.date(), use_cache=not force_send)

    # ---------- 发送 ----------
    recipients = [r.strip() for r in settings.email_recipient.split(",") if r.strip()]
    logger.info("send recipients=%s subject=%r", recipients, subject)
    send_html_email(
        sender=settings.qq_email_address,
        sender_display_name="每日期刊",
        auth_code=settings.qq_email_auth_code,
        recipient=recipients,
        subject=subject,
        html_body=html,
        inline_images=inline_images,
    )

    # 邮件发送成功后才提交 figures 7 天去重 state — 失败时下次 run
    # 仍能重新评估同批候选,避免"LLM 失败 + state 已写"导致永久遗漏。
    try:
        figures.commit_pushed(figures_state_path, figures_pending_pushed)
    except Exception as exc:  # noqa: BLE001
        logger.warning("figures.commit_pushed_failed exc=%r", exc)

    try:
        frontier_labs.commit_pushed(frontier_labs_state_path, frontier_labs_pending_pushed)
    except Exception as exc:  # noqa: BLE001
        logger.warning("frontier_labs.commit_pushed_failed exc=%r", exc)

    try:
        company_news.commit_pushed(company_news_state_path, company_news_pending_pushed)
    except Exception as exc:  # noqa: BLE001
        logger.warning("company_news.commit_pushed_failed exc=%r", exc)

    try:
        macro_news.commit_pushed(macro_news_state_path, macro_news_pending_pushed)
    except Exception as exc:  # noqa: BLE001
        logger.warning("macro_news.commit_pushed_failed exc=%r", exc)

    try:
        buffett_13f.commit_pushed(buffett_13f_state_path, buffett_13f_pending_save)
    except Exception as exc:  # noqa: BLE001
        logger.warning("buffett_13f.commit_pushed_failed exc=%r", exc)

    logger.info("main.done  est_cost=¥%.4f", cost_cny)
    return 0


if __name__ == "__main__":
    sys.exit(main())
