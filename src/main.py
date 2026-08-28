"""
每日晨报主入口(M4 端到端,5 大模块原始数据 + LLM 加工)。

链路:
    config.HOLDINGS  →  collectors:
                          stocks / company_news / figures / macro_news
                          / buffett_13f / jiangsu_fuel / sentiment
                                    ↓
                          translator(标题英→中)
                                    ↓
                          processors:
                          news_summarizer / macro_filter
                          / figure_filter / sentiment_judge
                                    ↓
                          renderer/render(段落 + 受控占位语 fallback)
                                    ↓
                          sender/smtp_sender.send_html_email
                                    (含 logo inline 附件)

时区:全程内部用 UTC,展示与邮件标题用北京时间。
LLM 调用失败时各区块独立降级；宏观视野不展示未加工 RSS 列表。
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
    jiangsu_fuel,
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
from src.processors.llm_client import LLMClient
from src.processors.thesis import consolidation as thesis_consolidation
from src.processors.thesis import extractor as thesis_extractor
from src.processors.thesis import renderer as thesis_renderer
from src.processors.thesis import rules as thesis_rules
from src.processors.thesis import state as thesis_state
from src.renderer.render import render_email
from src.sender.smtp_sender import InlineImage, send_html_email
from src.settings import load_settings
from src.utils.dates import now_beijing
from src.utils.delivery import clear_delivery_receipt, write_delivery_receipt
from src.utils.holidays import should_send_today
from src.utils.idempotency import already_sent_today
from src.utils.secrets import mask_emails
from src.valuation.models import FreshnessResult, ValuationDisplay
from src.valuation.service import commit_published_values, prepare_valuation_displays

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_LOGOS_DIR = _PROJECT_ROOT / "assets" / "logos"
_STATE_DIR = _PROJECT_ROOT / "state"
_DEFAULT_QUALITY_ALERT_PATH = _PROJECT_ROOT / ".quality-alert.txt"

_MACRO_PROCESSING_FALLBACK_NOTE = "宏观信息整理未完成，本期从略。"
_MACRO_SOURCE_FALLBACK_NOTE = "宏观数据源暂不可用，本期从略。"
_COMPANY_PROCESSING_FALLBACK_NOTE = "个股动态整理未完成，本期从略。"
_COMPANY_SOURCE_FALLBACK_NOTE = "个股动态数据源暂不可用，本期从略。"
_FIGURE_PROCESSING_FALLBACK_NOTE = "关键发言整理未完成，本期从略。"
_FRONTIER_PROCESSING_FALLBACK_NOTE = "前沿动态整理未完成，本期从略。"

_ACTIVE_THESIS_STATUS_RANK = {
    "core": 0,
    "emerging": 1,
    "candidate": 2,
    "stable": 3,
    "dormant": 4,
}
_ACTIVE_THESIS_THEME_LIMIT = 120


def _quality_alert_path() -> Path:
    configured = os.environ.get("QUALITY_ALERT_PATH", "").strip()
    return Path(configured) if configured else _DEFAULT_QUALITY_ALERT_PATH


def _clear_quality_alert() -> None:
    _quality_alert_path().unlink(missing_ok=True)


def _record_quality_alert(message: str) -> None:
    path = _quality_alert_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    previous = path.read_text(encoding="utf-8") if path.exists() else ""
    path.write_text(f"{previous}{message.strip()}\n", encoding="utf-8")
    logger.warning("quality.alert %s", message)


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
        sha8 = hashlib.sha1(path.read_bytes(), usedforsecurity=False).hexdigest()[:8]
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

    clear_delivery_receipt()
    _clear_quality_alert()
    settings = load_settings()
    now_bj = now_beijing()

    # 生产邮件只使用经人工验收的固定版本。估值新财报复核与后续所有文本处理
    # 共用同一客户端和总 token/时间预算；最终估值始终由 Python 复算。
    deepseek_model = settings.deepseek_model
    logger.info("llm.model_pinned model=%s", deepseek_model)
    llm = LLMClient(
        api_key=settings.deepseek_api_key,
        model=deepseek_model,
        total_timeout_seconds=720.0 if settings.valuation_enabled else 480.0,
    )

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

    # 估值官方源第一遍检查：尽早发现收盘后刚发布的财报，并自动刷新可复算底稿。
    valuation_displays: dict[str, ValuationDisplay] | None = None
    valuation_freshness: dict[str, FreshnessResult] | None = None
    if settings.valuation_enabled:
        logger.info("valuation.freshness_precheck")
        valuation_displays, valuation_freshness = prepare_valuation_displays(
            signals=signals,
            state_dir=_STATE_DIR,
            config_dir=_PROJECT_ROOT / "config",
            download_original=True,
            reviewer=llm,
        )

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
    fig_bundles, figures_pending_pushed = figures.fetch_all(
        state_path=figures_state_path,
        finnhub_api_key=settings.finnhub_api_key,
    )

    logger.info("collect.frontier_labs")
    frontier_labs_state_path = _STATE_DIR / "pushed_frontier_labs.json"
    frontier_labs_bundles, frontier_labs_pending_pushed = frontier_labs.fetch_all(
        state_path=frontier_labs_state_path,
        finnhub_api_key=settings.finnhub_api_key,
    )

    logger.info("collect.buffett_13f")
    buffett_13f_state_path = _STATE_DIR / "last_13f.json"
    buffett_bundle, buffett_13f_pending_save = buffett_13f.fetch(state_path=buffett_13f_state_path)

    logger.info("collect.jiangsu_fuel")
    jiangsu_fuel_alert = jiangsu_fuel.fetch(
        today=now_bj.date(),
        fred_api_key=settings.fred_api_key,
    )
    if (
        jiangsu_fuel_alert is not None
        and jiangsu_fuel_alert.forecast_method == "schedule_only"
    ):
        _record_quality_alert(
            "油价预告方向降级：新闻预测与国际原油代理均不可用，"
            "已保证显示调价时间和方向待更新。"
        )

    logger.info("collect.sentiment")
    sentiment_bundle = sentiment.fetch_all(
        settings.fred_api_key,
        state_dir=_STATE_DIR,
        today=now_bj.date(),
    )

    # ---------- LLM 处理(M4) ----------

    logger.info("translate.titles")
    _translate_all_bundles(
        cn_bundles=cn_bundles,
        fig_bundles=fig_bundles,
        macro_bundles=macro_bundles,
        client=llm,
    )

    logger.info("processors.news_summarizer")
    company_news_silence_note = None
    company_news_fallback_note = None
    company_source_failures = sum(1 for bundle in cn_bundles if bundle.error)
    if any(bundle.items for bundle in cn_bundles):
        company_news_summary = news_summarizer.summarize(cn_bundles, client=llm)
        if company_news_summary is not None and getattr(company_news_summary, "is_silence", False):
            company_news_summary = None
            company_news_silence_note = (
                news_summarizer.generate_silence_note(client=llm)
                or "商海无波，舟自徐行。"
            )
        elif company_news_summary is None:
            company_news_fallback_note = _COMPANY_PROCESSING_FALLBACK_NOTE
            _record_quality_alert(
                "个股动态加工失败：已使用受控占位语，未展示原始新闻列表。"
            )
    elif any(bundle.error for bundle in cn_bundles):
        company_news_summary = None
        company_news_fallback_note = _COMPANY_SOURCE_FALLBACK_NOTE
        _record_quality_alert("个股动态数据源不可用：已使用受控占位语。")
    else:
        company_news_summary = None
        company_news_silence_note = (
            news_summarizer.generate_silence_note(client=llm)
            or "商海无波，舟自徐行。"
        )
    if company_source_failures and any(bundle.items for bundle in cn_bundles):
        _record_quality_alert(
            f"个股动态部分数据源不可用：{company_source_failures} 个持仓仅展示其余有效内容。"
        )

    logger.info("processors.macro_filter")
    macro_news_silence_note = None
    macro_news_fallback_note = None
    macro_source_failures = sum(1 for bundle in macro_bundles if bundle.error)
    if any(bundle.items for bundle in macro_bundles):
        macro_news_summary = macro_filter.summarize(macro_bundles, client=llm)
        if macro_news_summary is None:
            macro_news_fallback_note = _MACRO_PROCESSING_FALLBACK_NOTE
            _record_quality_alert(
                "宏观视野加工失败：已使用受控占位语，未展示原始 RSS 列表。"
            )
    elif any(bundle.error for bundle in macro_bundles):
        macro_news_summary = None
        macro_news_fallback_note = _MACRO_SOURCE_FALLBACK_NOTE
        _record_quality_alert(
            "宏观视野数据源不可用：已使用受控占位语。"
        )
    else:
        macro_news_summary = None
        macro_news_silence_note = (
            macro_filter.generate_silence_note(client=llm)
            or "四海无波，日升月落而已。"
        )
    if macro_source_failures and any(bundle.items for bundle in macro_bundles):
        _record_quality_alert(
            f"宏观视野部分数据源不可用：{macro_source_failures} 个来源未参与摘要。"
        )

    logger.info("processors.figure_filter")
    figure_results = figure_filter.filter_all(fig_bundles, client=llm)
    figure_failures = [summary for summary in figure_results if summary.error]
    figure_summaries = [summary for summary in figure_results if summary.items]
    # M5.10 质量门槛:无 items 的人物(规则层全砍 / LLM 全 no)整个不渲染
    if len(figure_summaries) < len(figure_results):
        logger.info(
            "figure_filter.dropped_silent count=%d failures=%d",
            len(figure_results) - len(figure_summaries), len(figure_failures),
        )
    # 全员沉默时:LLM 写一句古典韵味的占位语
    figure_silence_note = None
    figure_fallback_note = None
    figure_footnotes = []
    if not figure_summaries:
        if figure_failures:
            figure_fallback_note = _FIGURE_PROCESSING_FALLBACK_NOTE
            _record_quality_alert(
                f"关键发言加工失败：{len(figure_failures)} 位人物处理未完成，"
                "已使用受控占位语。"
            )
        else:
            figure_silence_note = (
                figure_filter.generate_silence_note(llm)
                or "群贤皆默，市自为声。"
            )
    else:
        if figure_failures:
            _record_quality_alert(
                f"关键发言部分降级：{len(figure_failures)} 位人物处理未完成，"
                "已展示其余有效内容。"
            )
        # 版面限流:质量评分后最多展示 3 位人物
        figure_summaries = figure_filter.select_voice_summaries(figure_summaries)
        # 跨人物统一编号 [1] [2] ...,章节底部一次列出所有来源
        figure_footnotes = figure_filter.assign_footnotes(figure_summaries)

    logger.info("processors.sentiment_judge")
    sentiment_verdict = sentiment_judge.judge(sentiment_bundle, client=llm)
    if sentiment_verdict and sentiment_verdict.get("argument_fallback"):
        _record_quality_alert("市场情绪文字说明加工失败：已使用确定性说明。")

    logger.info("processors.frontier_labs_filter")
    frontier_labs_items, frontier_labs_failures = frontier_labs_filter.filter_all_with_status(
        frontier_labs_bundles,
        client=llm,
    )
    frontier_labs_fallback_note = None
    if frontier_labs_failures:
        if not frontier_labs_items:
            frontier_labs_fallback_note = _FRONTIER_PROCESSING_FALLBACK_NOTE
        _record_quality_alert(
            f"前沿动态加工失败：{len(frontier_labs_failures)} 个实验室处理未完成。"
        )

    logger.info("processors.thesis")
    try:
        migration = thesis_consolidation.migrate_history_if_needed(
            _STATE_DIR,
            today=now_bj.date(),
        )
        if migration.applied:
            logger.info(
                "thesis.history_migrated evidence=%d changed=%d themes=%d",
                migration.evidence_count,
                migration.changed_count,
                migration.theme_count,
            )
        # 第一次读 state：获取 active themes 用于注入 prompt（防 theme 漂移）
        state_dict = thesis_state.load_state(_STATE_DIR)
        active_themes = _select_active_thesis_themes(state_dict)

        evidence_today, thesis_extraction_error = thesis_extractor.extract_with_status(
            client=llm,
            company_news=company_news_summary,
            macro_news=macro_news_summary,
            figure_summaries=figure_summaries,
            berkshire_events=buffett_bundle,
            frontier_labs_events=frontier_labs_items,
            active_themes=active_themes,
            today=now_bj.date(),
        )
        if thesis_extraction_error:
            _record_quality_alert(
                "长期判断证据提取失败：本次未写入新的判断证据。"
            )
        # extractor 只 return；写入由 state.py 统一负责（内部按 evidence_id 去重）
        thesis_state.append_evidence(evidence_today, _STATE_DIR, today=now_bj.date())

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

        judgment_section = thesis_renderer.build_judgment_section(
            thesis_events,
            state=state_dict,
            evidence_today=evidence_today,
            today=now_bj.date(),
        )
    except Exception as exc:  # noqa: BLE001 — 任何 thesis 步骤失败都降级到无 judgment_section
        # 与项目其他异常处理对齐:不用 exc_info=True / logger.exception,因 OpenAI SDK 异常
        # traceback 可能带请求 url 或 header 痕迹;只记 type + 截断后的 str。
        logger.warning(
            "thesis.pipeline_failed exc_type=%s msg=%s",
            type(exc).__name__, str(exc)[:200],
        )
        _record_quality_alert("长期判断管线失败：已跳过本次判断更新。")
        judgment_section = None

    logger.info("processors.holdings_intro")
    holdings_intro_text = holdings_intro.write_intro(signals, client=llm)
    if signals and holdings_intro_text is None:
        _record_quality_alert("持仓引言加工失败：已使用确定性说明。")

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
    # 发送前第二遍检查，封住“第一遍检查后、邮件渲染前发布新财报”的竞态窗口；
    # 这里只复核文件编号，不重复刷新财务 provider。
    if settings.valuation_enabled:
        logger.info("valuation.freshness_final_check")
        valuation_displays, valuation_freshness = prepare_valuation_displays(
            signals=signals,
            state_dir=_STATE_DIR,
            config_dir=_PROJECT_ROOT / "config",
            download_original=False,
            prior_freshness=valuation_freshness,
            reviewer=llm,
        )
        publishable = sum(
            1 for value in (valuation_displays or {}).values() if not value.is_pending
        )
        if publishable < len(HOLDINGS):
            _record_quality_alert(
                f"内在价值数据未完全就绪：{publishable}/{len(HOLDINGS)} 只通过官方源与复算闸门。"
            )
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
        valuations=valuation_displays,
        valuation_checked_at=(
            max((item.checked_at for item in valuation_freshness.values()), default=None)
            if valuation_freshness
            else None
        ),
        # 加工产物；失败区块使用受控占位语，不展示未经筛选的原始列表。
        sentiment=sentiment_bundle,
        sentiment_verdict=sentiment_verdict,
        company_news=cn_bundles,
        company_news_summary=company_news_summary,
        company_news_fallback_note=company_news_fallback_note,
        figures=fig_bundles,
        figure_summaries=figure_summaries,
        figure_silence_note=figure_silence_note,
        figure_fallback_note=figure_fallback_note,
        figure_footnotes=figure_footnotes,
        macro_news=macro_bundles,
        macro_news_summary=macro_news_summary,
        company_news_silence_note=company_news_silence_note,
        macro_news_silence_note=macro_news_silence_note,
        macro_news_fallback_note=macro_news_fallback_note,
        buffett_13f=buffett_bundle,
        jiangsu_fuel_alert=jiangsu_fuel_alert,
        frontier_labs_items=frontier_labs_items,
        frontier_labs_fallback_note=frontier_labs_fallback_note,
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
    # 收件人邮箱不全写日志,用 mask_emails 只留首字母 + 域名,降低 PII 在日志被
    # actions/cache 持久化或外泄到第三方监控的风险(仓库虽 PRIVATE 但日志可能跨边界传递)
    logger.info("send recipients=%s subject=%r", mask_emails(recipients), subject)
    delivery = send_html_email(
        sender=settings.qq_email_address,
        sender_display_name="每日期刊",
        auth_code=settings.qq_email_auth_code,
        recipient=recipients,
        subject=subject,
        html_body=html,
        inline_images=inline_images,
    )
    write_delivery_receipt(
        sent_at=now_bj,
        accepted_count=len(delivery.accepted),
        refused_count=len(delivery.refused),
        run_id=os.environ.get("GH_RUN_ID"),
    )
    try:
        commit_published_values(
            valuation_displays,
            state_dir=_STATE_DIR,
            sent_at=now_bj,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("valuation.commit_published_failed exc=%r", exc)

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
