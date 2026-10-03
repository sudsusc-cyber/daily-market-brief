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

import json
import logging
import os
import sys
from dataclasses import replace
from datetime import UTC, datetime
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
from src.config import COMPANY_HOLDINGS, HOLDINGS, Holding
from src.processors import (
    figure_filter,
    frontier_labs_filter,
    holdings_intro,
    macro_filter,
    news_summarizer,
    sentiment_judge,
    translator,
)
from src.processors.editorial_history import EditorialHistory
from src.processors.llm_client import LLMClient
from src.processors.news_selection import (
    company_candidate,
    frontier_candidate,
    macro_candidate,
    meaningful_quote,
)
from src.processors.thesis import renderer as thesis_renderer
from src.renderer.render import render_email
from src.sender.smtp_sender import InlineImage, send_html_email
from src.settings import load_settings
from src.utils.brief_audit import archive_delivery, archive_publication, content_report
from src.utils.dates import now_beijing
from src.utils.delivery import clear_delivery_receipt, write_delivery_receipt
from src.utils.holidays import should_send_today
from src.utils.idempotency import already_sent_today
from src.utils.publication import candidate_sources, published_pending, summary_urls
from src.utils.runtime_budget import RuntimeBudget, llm_wall_timeout_seconds
from src.utils.secrets import mask_emails
from src.valuation.models import FreshnessResult, ValuationDisplay
from src.valuation.morningstar import MorningstarPublicProvider
from src.valuation.qqqm import cached_qqqm_display, prepare_qqqm_display
from src.valuation.service import (
    cached_morningstar_displays,
    commit_published_values,
    prepare_valuation_displays,
)

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_LOGOS_DIR = _PROJECT_ROOT / "assets" / "logos"
_STATE_DIR = _PROJECT_ROOT / "state"
_DEFAULT_QUALITY_ALERT_PATH = _PROJECT_ROOT / ".quality-alert.txt"

_MACRO_PROCESSING_FALLBACK_NOTE = "宏观信息整理未完成，本期从略。"
_MACRO_SOURCE_FALLBACK_NOTE = "宏观数据源暂不可用，本期从略。"
_COMPANY_PROCESSING_FALLBACK_NOTE = "个股动态整理未完成，本期从略。"
_COMPANY_SOURCE_FALLBACK_NOTE = "个股动态数据源暂不可用，本期从略。"
_FIGURE_PROCESSING_FALLBACK_NOTE = "关键发言筛选暂不可用，本期暂不刊载。"

_ACTIVE_THESIS_STATUS_RANK = {
    "core": 0,
    "emerging": 1,
    "candidate": 2,
    "stable": 3,
    "dormant": 4,
}
_ACTIVE_THESIS_THEME_LIMIT = 120


def _verified_timestamp(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        timestamp = datetime.fromisoformat(raw)
        if timestamp.tzinfo is None:
            return None
        return timestamp.astimezone(UTC)
    except (ValueError, TypeError):
        return None


def _valuation_recency(value: ValuationDisplay) -> tuple[str, datetime]:
    try:
        source_date = datetime.fromisoformat(value.financial_as_of or "").date().isoformat()
    except (ValueError, TypeError):
        source_date = ""
    return source_date, _verified_timestamp(value.verified_at) or datetime.min.replace(tzinfo=UTC)


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
        rows.append(
            (
                _ACTIVE_THESIS_STATUS_RANK[status],
                getattr(st, "last_evidence_date", "") or "",
                str(theme),
            )
        )

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
    frontier_bundles: list | None = None,
) -> None:
    """按实际选稿窗口翻译，原始标题/摘要保持不变。"""
    from src.collectors.news_context import enrich_speaker_context, enrich_technical_context
    enrich_technical_context([item for bundle in [*cn_bundles, *fig_bundles, *macro_bundles, *(frontier_bundles or [])]
                              for item in bundle.items])
    enrich_speaker_context(fig_bundles)
    titles_to_translate: list[object] = []
    for b in cn_bundles:
        # Bind the holding before excerpt selection/translation, not only when
        # rendering: otherwise an unrelated first sentence gets translated.
        for item in b.items:
            item.holding_ticker = b.holding.ticker
        titles_to_translate.extend([item for item in b.items if company_candidate(item, b.holding.ticker)][:5])
    for f in fig_bundles:
        titles_to_translate.extend([item for item in f.items if meaningful_quote(item)][:5])
    for m in macro_bundles:
        titles_to_translate.extend([item for item in m.items if macro_candidate(item)][:8])
    for bundle in frontier_bundles or []:
        titles_to_translate.extend([item for item in bundle.items if frontier_candidate(item)][:8])
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
            (p for ext in ("png", "jpg", "jpeg") if (p := _LOGOS_DIR / f"{h.slug}.{ext}").exists()),
            None,
        )
        if path is None:
            logger.warning(
                "logo.missing ticker=%s expected=%s/{png,jpg}", h.ticker, _LOGOS_DIR / h.slug
            )
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
    budget = RuntimeBudget()
    stage_timeouts = []

    def timed_out(label, value):
        stage_timeouts.append(label)
        _record_quality_alert(f"{label}取数超时：已停止等待并降级，保留发送时间。")
        return value

    # 生产邮件只使用经人工验收的固定版本。估值新财报复核与后续所有文本处理
    # 共用同一客户端和总 token/时间预算；最终估值始终由 Python 复算。
    deepseek_model = settings.deepseek_model
    logger.info("llm.model_pinned model=%s", deepseek_model)
    llm = LLMClient(
        api_key=settings.deepseek_api_key,
        model=deepseek_model,
        total_timeout_seconds=720.0 if settings.valuation_enabled else 480.0,
        wall_timeout_seconds=llm_wall_timeout_seconds(),
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
    collected_signals: dict[str, stocks.StockSignal] = {}

    def stock_progress(result: stocks.StockSignal) -> None:
        collected_signals[result.holding.ticker] = result

    def stock_timeout() -> list[stocks.StockSignal]:
        return timed_out("行情", [
            collected_signals[h.ticker] if h.ticker in collected_signals
            else stocks._failed(h, "行情取数超时") for h in HOLDINGS
        ])

    signals = budget.call(stocks.fetch_all, HOLDINGS, seconds=150,
        fallback=stock_timeout, on_result=stock_progress)
    morningstar_provider = (
        MorningstarPublicProvider() if settings.morningstar_fair_value_enabled else None
    )

    # 估值官方源第一遍检查：尽早发现收盘后刚发布的财报，并自动刷新可复算底稿。
    valuation_displays: dict[str, ValuationDisplay] | None = None
    valuation_freshness: dict[str, FreshnessResult] | None = None
    qqqm_display: ValuationDisplay | None = None

    def valuation_timeout():
        old = (
            cached_morningstar_displays(
                state_dir=_STATE_DIR,
                config_dir=_PROJECT_ROOT / "config",
                checked_at=now_beijing(),
                prices={signal.holding.ticker: signal.last_close for signal in signals},
            )
            if morningstar_provider is not None
            else {}
        )
        # A failed final check must not overwrite a good persisted value with a
        # pending result from the first pass.
        for ticker, value in (valuation_displays or {}).items():
            if not value.is_pending and (
                ticker not in old or old[ticker].is_pending
                or _valuation_recency(value) >= _valuation_recency(old[ticker])
            ):
                old[ticker] = value
        if qqqm_display is not None:
            old["QQQM"] = qqqm_display
        for holding in COMPANY_HOLDINGS:
            old.setdefault(
                holding.ticker,
                ValuationDisplay(
                    ticker=holding.ticker, status="source_unavailable", value_label="公允价值"
                ),
            )
        return timed_out(
            "估值复核",
            (
                {
                    ticker: replace(
                        value,
                        data_note=(
                            (value.data_note or f"{ticker} 复核未完成，沿用较早核验")
                            if not value.is_pending
                            else None
                        ),
                        status="not_due" if not value.is_pending else "source_unavailable",
                    )
                    for ticker, value in old.items()
                },
                valuation_freshness or {},
            ),
        )

    if settings.valuation_enabled:
        qqqm_signal = next(
            (signal for signal in signals if signal.holding.ticker == "QQQM" and signal.last_close),
            None,
        )
        if qqqm_signal is not None:
            logger.info("valuation.qqqm_prepare price=%.4f", qqqm_signal.last_close)
            qqqm_display = budget.call(prepare_qqqm_display,
                seconds=75,
                fallback=lambda: timed_out("QQQM", cached_qqqm_display(
                    price=qqqm_signal.last_close, state_dir=_STATE_DIR, checked_at=now_beijing())),
                price=qqqm_signal.last_close,
                client=llm,
                state_dir=_STATE_DIR,
                checked_at=now_bj,
            )
        else:
            logger.warning("valuation.qqqm_retained reason=market_price_unavailable")
            # Value calculation is independent of the current market price.
            # Retain it even when the quote is missing; do not fabricate a gap.
            qqqm_display = replace(cached_qqqm_display(
                price=1.0, state_dir=_STATE_DIR, checked_at=now_bj,
                reason="行情暂不可用"), implied_return=None)
        logger.info("valuation.freshness_precheck")
        valuation_displays, valuation_freshness = budget.call(prepare_valuation_displays,
            seconds=360, fallback=valuation_timeout,
            signals=signals,
            state_dir=_STATE_DIR,
            config_dir=_PROJECT_ROOT / "config",
            checked_at=now_bj,
            download_original=True,
            reviewer=llm,
            morningstar_provider=morningstar_provider,
            qqqm_display=qqqm_display,
        )

    logger.info("collect.company_news")
    company_news_state_path = _STATE_DIR / "pushed_company_news.json"
    cn_bundles, company_news_pending_pushed = budget.call(company_news.fetch_all,
        COMPANY_HOLDINGS,
        settings.finnhub_api_key,
        seconds=90,
        fallback=lambda: timed_out("个股新闻", ([company_news.CompanyNewsBundle(h, error="取数超时")
                                                  for h in COMPANY_HOLDINGS],
                                                 company_news._load_pushed_news(company_news_state_path))),
        state_path=company_news_state_path,
    )

    logger.info("collect.macro_news")
    macro_news_state_path = _STATE_DIR / "pushed_macro_news.json"
    macro_bundles, macro_news_pending_pushed = budget.call(macro_news.fetch_all,
        seconds=60,
        fallback=lambda: timed_out("宏观新闻", ([macro_news.MacroFeedBundle("数据源", error="取数超时")],
                                                 macro_news._load_pushed_macro(macro_news_state_path))),
        state_path=macro_news_state_path
    )

    logger.info("collect.figures")
    figures_state_path = _STATE_DIR / "pushed_figures.json"
    fig_bundles, figures_pending_pushed = budget.call(figures.fetch_all,
        seconds=90,
        fallback=lambda: timed_out("关键发言", ([figures.FigureBundle("数据源", "", error="取数超时")],
                                                 figures._load_pushed(figures_state_path))),
        state_path=figures_state_path,
        finnhub_api_key=settings.finnhub_api_key,
    )

    logger.info("collect.frontier_labs")
    frontier_labs_state_path = _STATE_DIR / "pushed_frontier_labs.json"
    frontier_labs_bundles, frontier_labs_pending_pushed = budget.call(frontier_labs.fetch_all,
        seconds=60,
        fallback=lambda: timed_out("前沿动态", ([frontier_labs.FrontierBundle("数据源", [], errors=["取数超时"])],
                                                 frontier_labs._load_pushed(frontier_labs_state_path))),
        state_path=frontier_labs_state_path,
        finnhub_api_key=settings.finnhub_api_key,
    )

    logger.info("collect.buffett_13f")
    buffett_13f_state_path = _STATE_DIR / "last_13f.json"
    buffett_bundle, buffett_13f_pending_save = budget.call(buffett_13f.fetch,
        state_path=buffett_13f_state_path, seconds=30,
        fallback=lambda: timed_out("13F", (buffett_13f.BuffettBundle(error="取数超时"), None)))

    logger.info("collect.jiangsu_fuel")
    jiangsu_fuel_alert = budget.call(jiangsu_fuel.fetch,
        seconds=45, fallback=lambda: timed_out("油价", None),
        today=now_bj.date(),
        fred_api_key=settings.fred_api_key,
    )
    if jiangsu_fuel_alert is not None and jiangsu_fuel_alert.forecast_method == "schedule_only":
        _record_quality_alert(
            "油价预告方向降级：新闻预测与国际原油代理均不可用，" "已保证显示调价时间和方向待更新。"
        )

    logger.info("collect.sentiment")
    sentiment_bundle = budget.call(sentiment.fetch_all,
        settings.fred_api_key,
        seconds=60,
        fallback=lambda: timed_out("情绪指标", sentiment.SentimentBundle([], now_beijing())),
        state_dir=_STATE_DIR,
        today=now_bj.date(),
    )

    # ---------- LLM 处理(M4) ----------
    publication_candidates = {
        "company": candidate_sources(cn_bundles, lambda b: b.holding.ticker, company_news._content_hash),
        "macro": candidate_sources(macro_bundles, lambda b: b.source, macro_news._content_hash),
        "figures": candidate_sources(fig_bundles, lambda b: b.person, figures._content_hash),
        "frontier": candidate_sources(frontier_labs_bundles, lambda b: b.lab, frontier_labs._content_hash),
    }

    logger.info("translate.titles")
    _translate_all_bundles(
        cn_bundles=cn_bundles,
        fig_bundles=fig_bundles,
        macro_bundles=macro_bundles,
        frontier_bundles=frontier_labs_bundles,
        client=llm,
    )

    logger.info("processors.news_summarizer")
    editorial_history = EditorialHistory(_STATE_DIR / "published_editorial.json", now_bj.date())
    company_news_silence_note = None
    company_news_fallback_note = None
    company_source_failures = sum(1 for bundle in cn_bundles if bundle.error)
    if any(bundle.items for bundle in cn_bundles):
        company_news_summary = news_summarizer.summarize(cn_bundles, client=llm, history=editorial_history)
        if company_news_summary is not None and getattr(company_news_summary, "is_silence", False):
            company_news_summary = None
            company_news_silence_note = (
                news_summarizer.generate_silence_note(client=llm) or "商海无波，舟自徐行。"
            )
        elif company_news_summary is None:
            company_news_fallback_note = _COMPANY_PROCESSING_FALLBACK_NOTE
            _record_quality_alert("个股动态加工失败：已使用受控占位语，未展示原始新闻列表。")
    elif any(bundle.error for bundle in cn_bundles):
        company_news_summary = None
        company_news_fallback_note = _COMPANY_SOURCE_FALLBACK_NOTE
        _record_quality_alert("个股动态数据源不可用：已使用受控占位语。")
    else:
        company_news_summary = None
        company_news_silence_note = (
            news_summarizer.generate_silence_note(client=llm) or "商海无波，舟自徐行。"
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
            _record_quality_alert("宏观视野加工失败：已使用受控占位语，未展示原始 RSS 列表。")
    elif any(bundle.error for bundle in macro_bundles):
        macro_news_summary = None
        macro_news_fallback_note = _MACRO_SOURCE_FALLBACK_NOTE
        _record_quality_alert("宏观视野数据源不可用：已使用受控占位语。")
    else:
        macro_news_summary = None
        macro_news_silence_note = (
            macro_filter.generate_silence_note(client=llm) or "四海无波，日升月落而已。"
        )
    if macro_source_failures and any(bundle.items for bundle in macro_bundles):
        _record_quality_alert(
            f"宏观视野部分数据源不可用：{macro_source_failures} 个来源未参与摘要。"
        )

    logger.info("processors.figure_filter")
    figure_results = figure_filter.filter_all(fig_bundles, client=llm, history=editorial_history)
    figure_failures = [summary for summary in figure_results if summary.error]
    figure_summaries = [summary for summary in figure_results if summary.items]
    # M5.10 质量门槛:无 items 的人物(规则层全砍 / LLM 全 no)整个不渲染
    if len(figure_summaries) < len(figure_results):
        logger.info(
            "figure_filter.dropped_silent count=%d failures=%d",
            len(figure_results) - len(figure_summaries),
            len(figure_failures),
        )
    # 全员沉默时:LLM 写一句古典韵味的占位语
    figure_silence_note = None
    figure_fallback_note = None
    figure_footnotes = []
    if not figure_summaries:
        if figure_failures:
            figure_fallback_note = (
                _FIGURE_PROCESSING_FALLBACK_NOTE if any(s.processing_error for s in figure_failures)
                else "关键发言翻译处理未完成，本期暂未刊出。" if all(s.failure_kind == "translation" for s in figure_failures)
                else "关键发言候选内容未通过核验，本期暂不刊载。" if any(s.content_rejections for s in figure_failures)
                else "关键发言来源读取失败，本期暂不刊载。"
            )
            _record_quality_alert(
                f"关键发言存在来源、筛选或核验缺口：{len(figure_failures)} 位人物，已显示对应提示。"
            )
        else:
            figure_silence_note = figure_filter.generate_silence_note(llm) or "群贤皆默，市自为声。"
    else:
        if figure_failures:
            _record_quality_alert(
                f"关键发言部分降级：{len(figure_failures)} 位人物存在来源、筛选或核验缺口，"
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
    frontier_report = frontier_labs_filter.filter_all_report(
        frontier_labs_bundles,
        client=llm,
    )
    macro_news_summary, frontier_report.items, frontier_merged_urls = macro_filter.merge_frontier_duplicates(
        macro_news_summary, frontier_report.items,
    )
    frontier_labs_items = frontier_report.items
    frontier_labs_fallback_note = frontier_report.fallback_note
    if frontier_report.failures:
        _record_quality_alert(
            f"前沿动态状态={frontier_report.state}："
            f"来源异常 {len(frontier_report.source_failures)}，"
            f"筛选异常 {len(frontier_report.processing_failures)}，"
            f"候选核验拒绝 {len(frontier_report.content_rejections)}。"
        )

    logger.info("processors.thesis")
    judgment_audit = {}
    judgment_error = None
    try:
        judgment_sources = thesis_renderer.publication_sources(
            company_news=None if company_news_fallback_note or company_news_silence_note else company_news_summary,
            macro_news=None if macro_news_fallback_note or macro_news_silence_note else macro_news_summary,
            figure_summaries=figure_summaries if fig_bundles else [],
            frontier_labs_events=[] if frontier_labs_fallback_note else frontier_labs_items[:2],
        )
        judgment_section = thesis_renderer.build_judgment_section(
            sources=judgment_sources, today=now_bj.date(),
            history=thesis_renderer.load_publications(_STATE_DIR), selection_audit=judgment_audit,
        )
        judgment_section = thesis_renderer.validate_publication(
            judgment_section, sources=judgment_sources, today=now_bj.date(),
        )
    except Exception as exc:  # noqa: BLE001 — 任何 thesis 步骤失败都降级到无 judgment_section
        # 与项目其他异常处理对齐:不用 exc_info=True / logger.exception,因 OpenAI SDK 异常
        # traceback 可能带请求 url 或 header 痕迹;只记 type + 截断后的 str。
        logger.warning(
            "thesis.pipeline_failed exc_type=%s msg=%s",
            type(exc).__name__,
            str(exc)[:200],
        )
        _record_quality_alert("长期判断管线失败：已跳过本次判断更新。")
        judgment_error = type(exc).__name__
        judgment_section = None

    logger.info("processors.holdings_intro")
    holdings_intro_text = holdings_intro.write_intro(signals, client=llm, history=editorial_history)
    if signals and holdings_intro_text is None:
        _record_quality_alert("持仓引言加工失败：已使用确定性说明。")

    # ---------- 刊头图(M5) ----------
    logger.info("collect.header_image")
    header = budget.call(header_image.pick_header_image, now_bj.date(), seconds=25,
                         fallback=header_image._tier3_local)

    # ---------- 渲染 ----------
    logger.info("render")
    # 发送前第二遍检查，封住“第一遍检查后、邮件渲染前发布新财报”的竞态窗口；
    # Morningstar 在真实晚时点重读；旧的自算模型仍只复核财报编号。
    if settings.valuation_enabled:
        if qqqm_signal is not None and (qqqm_display is None or qqqm_display.status != "current"):
            logger.info("valuation.qqqm_final_retry")
            first_qqqm = qqqm_display
            retry_qqqm = budget.call(
                prepare_qqqm_display, seconds=75,
                fallback=lambda: timed_out("QQQM 发送前复核", cached_qqqm_display(
                    price=qqqm_signal.last_close, state_dir=_STATE_DIR, checked_at=now_beijing())),
                price=qqqm_signal.last_close, client=llm, state_dir=_STATE_DIR,
                checked_at=now_beijing(),
            )
            qqqm_display = (first_qqqm if first_qqqm is not None and not first_qqqm.is_pending
                            and retry_qqqm.is_pending else retry_qqqm)
        logger.info("valuation.freshness_final_check")
        valuation_displays, valuation_freshness = budget.call(prepare_valuation_displays,
            seconds=360, fallback=valuation_timeout,
            signals=signals,
            state_dir=_STATE_DIR,
            config_dir=_PROJECT_ROOT / "config",
            checked_at=now_beijing(),
            download_original=False,
            prior_freshness=valuation_freshness,
            reviewer=llm,
            morningstar_provider=morningstar_provider,
            qqqm_display=qqqm_display,
        )
        publishable = sum(
            1 for value in (valuation_displays or {}).values() if not value.is_pending
        )
        expected_valuations = len(HOLDINGS)
        if publishable < expected_valuations:
            _record_quality_alert(
                f"{'公允价值' if settings.morningstar_fair_value_enabled else '内在价值'}"
                f"数据未完全就绪：{publishable}/{expected_valuations} 只通过来源与复算闸门。"
            )
        for value in (valuation_displays or {}).values():
            if value.data_note:
                _record_quality_alert(value.data_note)
    logo_cids, inline_images = _load_logo_assets(HOLDINGS)
    # 刊头图统一走 inline CID(Android QQ 邮箱不会自动加载远程图,iOS/桌面正常)。
    # header_image.pick_header_image 三层都会下载到本地并返回 local_path。
    inline_images.append(
        InlineImage(
            cid="header_image",
            path=header["local_path"],
            subtype=None,
        )
    )
    html = render_email(
        signals=signals,
        generated_at=now_bj,
        logo_cids=logo_cids,
        header_image_url=header["url"],
        holdings_intro=holdings_intro_text,
        valuations=valuation_displays,
        valuation_checked_at=(
            min((timestamp for item in (valuation_displays or {}).values()
                 if (timestamp := _verified_timestamp(item.verified_at)) is not None), default=None)
            or min((item.checked_at for item in (valuation_freshness or {}).values()), default=None)
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
    subject = generate_subject(
        subject_data, llm=llm, today_bj=now_bj.date(), use_cache=not force_send
    )

    # 所有 LLM 调用（包括邮件主题）完成后再汇总，避免日用量少计。
    cum = llm.cumulative
    cost_cny = llm.estimate_cost_cny()
    llm.log_timing_summary()
    logger.info(
        "llm.summary input=%d output=%d reasoning=%d cache_hit=%d est_cost=¥%.4f",
        cum.input_tokens,
        cum.output_tokens,
        cum.reasoning_tokens,
        cum.cache_hit_tokens,
        cost_cny,
    )
    if cost_cny > 0.5:
        logger.warning(
            "llm.daily_budget_exceeded est_cost=¥%.4f budget=¥0.5000",
            cost_cny,
        )

    try:
        qqqm_diagnostics = json.loads((_STATE_DIR / "qqqm_calculation_audit.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        qqqm_diagnostics = {}
    report = content_report(
        signals=signals, valuations=valuation_displays, sentiment=sentiment_bundle,
        diagnostics={"QQQM": qqqm_diagnostics, "news_context": [
            {"title": item.title, "status": item.context_diagnostic,
             "context_url": getattr(item, "context_url", ""),
             "fetched_at": getattr(item, "context_fetched_at", "")}
            for bundle in [*cn_bundles, *macro_bundles, *fig_bundles, *frontier_labs_bundles]
            for item in bundle.items if getattr(item, "context_diagnostic", "")
        ][:20], "company": {
            "content_rejections": getattr(company_news_summary, "content_rejections", []),
            "selection": getattr(company_news_summary, "selection_audit", []),
        }, "macro_selection": getattr(macro_news_summary, "selection_audit", []), "frontier": {
            "source_failures": frontier_report.source_failures,
            "processing_failures": frontier_report.processing_failures,
            "content_rejections": frontier_report.content_rejections,
            "selection": frontier_report.selection_audit,
            "merged_into_macro": sorted(frontier_merged_urls),
        }, "figures": {summary.person: summary.content_rejections for summary in figure_results
                        if summary.content_rejections},
                        "figure_verification": {summary.person: summary.verification_audit
                                                for summary in figure_results if summary.verification_audit},
                        "figure_failure_kinds": {summary.person: summary.failure_kind
                                                 for summary in figure_results if summary.failure_kind}},
        expected_tickers=[holding.ticker for holding in HOLDINGS] if settings.valuation_enabled else [],
        expected_prices=[holding.ticker for holding in HOLDINGS],
        expected_metrics=sentiment.METRIC_NAMES,
        section_health={
            "company": {"source_failures": company_source_failures,
                        "content_rejections": len(getattr(company_news_summary, "content_rejections", [])),
                        "processing_failures": int(bool(getattr(company_news_summary, "error", None))
                                                   or bool(company_news_fallback_note and any(b.items for b in cn_bundles))),
                        "fallback": bool(company_news_fallback_note), "silence": bool(company_news_silence_note)},
            "macro": {"source_failures": macro_source_failures,
                      "content_rejections": len(getattr(macro_news_summary, "content_rejections", [])),
                      "processing_failures": int(bool(getattr(macro_news_summary, "error", None))
                                                 or bool(macro_news_fallback_note and any(b.items for b in macro_bundles))),
                      "fallback": bool(macro_news_fallback_note), "silence": bool(macro_news_silence_note)},
            "figures": {"source_failures": sum(bool(b.error) for b in fig_bundles),
                        "processing_failures": sum(bool(s.processing_error) for s in figure_results),
                        "content_rejections": sum(len(s.content_rejections) - s.translation_failure_count for s in figure_results),
                        "translation_failures": sum(s.translation_failure_count for s in figure_results),
                        "fallback": bool(figure_fallback_note),
                        "silence": not figure_summaries and not figure_failures},
            "frontier": frontier_report.health(),
            "sentiment": {"fallback": sentiment_verdict is None or bool(sentiment_verdict.get("argument_fallback"))},
            "judgment": {"processing_failures": int(judgment_error is not None)},
            "holdings_intro": {"fallback": bool(signals) and holdings_intro_text is None},
            "fuel": {"fallback": jiangsu_fuel_alert is not None and jiangsu_fuel_alert.forecast_method == "schedule_only"},
            "collection": {"timeout_count": len(stage_timeouts)},
        },
        news={
            "company": (sum(len(b.items) for b in cn_bundles), [company_news_summary]),
            "macro": (sum(len(b.items) for b in macro_bundles), [macro_news_summary]),
            "figures": (sum(len(b.items) for b in fig_bundles), [item for group in figure_summaries for item in group.items]),
            "frontier": (sum(len(b.items) for b in frontier_labs_bundles), frontier_labs_items),
        },
    )
    report["section_details"] = {
        "sentiment": sentiment_verdict,
        "company_error": getattr(company_news_summary, "error", None),
        "macro_error": getattr(macro_news_summary, "error", None),
        "judgment_error": judgment_error,
        "stage_timeouts": stage_timeouts,
    }
    from src.utils.quality_details import quality_details
    report["html_bytes"] = len(html.encode())
    report["quality_details"] = quality_details(report)
    report["judgment_mapping"] = judgment_section.items if judgment_section else []
    report["judgment_selection"] = judgment_audit
    audit_directory = archive_publication(html, generated_at=now_bj, report=report, inline_images=inline_images, subject=subject)
    if report["status"] == "degraded" or len(html.encode()) > 98304:
        for detail in report["quality_details"] or ["内容核验降级；详见结构化审计"]:
            _record_quality_alert(detail)

    if os.environ.get("BRIEF_PREVIEW_ONLY", "").lower() == "true":
        logger.info("preview.done audit=%s smtp_calls=0", audit_directory)
        return 0

    first_acceptance_at = None

    def delivery_progress(result):
        nonlocal audit_directory, first_acceptance_at
        if first_acceptance_at is None:
            first_acceptance_at = now_beijing()
        accepted_at = first_acceptance_at
        write_delivery_receipt(sent_at=accepted_at, accepted_count=len(result.accepted),
                               refused_count=len(result.refused), run_id=os.environ.get("GH_RUN_ID"))
        # SMTP acceptance must remain authoritative even if optional archiving fails.
        try:
            audit_directory = archive_delivery(audit_directory, {
                "status": "partial" if result.refused else "full", "accepted_count": len(result.accepted),
                "refused_count": len(result.refused), "sent_at": accepted_at.isoformat(),
                "edition": accepted_at.date().isoformat(),
            })
        except OSError as exc:
            _record_quality_alert(f"邮件留档发送状态更新失败：{type(exc).__name__}")

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
        on_progress=delivery_progress,
    )
    delivery_progress(delivery)
    try:
        thesis_renderer.commit_publications(judgment_section, _STATE_DIR, today=now_bj.date())
    except Exception as exc:  # noqa: BLE001
        _record_quality_alert(f"长期判断刊发记录保存失败：{type(exc).__name__}")
    try:
        commit_published_values(
            valuation_displays,
            state_dir=_STATE_DIR,
            sent_at=now_bj,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("valuation.commit_published_failed exc=%r", exc)

    try:
        editorial_history.capture(company_news_summary, figure_summaries)
        if holdings_intro_text:
            editorial_history.remember("holdings_intro", "", holdings_intro_text)
        editorial_history.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("editorial_history.commit_failed type=%s", type(exc).__name__)

    # 邮件发送成功后才提交 figures 7 天去重 state — 失败时下次 run
    # 仍能重新评估同批候选,避免"LLM 失败 + state 已写"导致永久遗漏。
    try:
        figures.commit_pushed(figures_state_path, published_pending(
            figures_pending_pushed, publication_candidates["figures"],
            {item.source_url for summary in figure_summaries for item in summary.items},
        ))
    except Exception as exc:  # noqa: BLE001
        logger.warning("figures.commit_pushed_failed exc=%r", exc)

    try:
        frontier_labs.commit_pushed(frontier_labs_state_path, published_pending(
            frontier_labs_pending_pushed, publication_candidates["frontier"],
            {item.source_url for item in frontier_labs_items[:2]} | frontier_merged_urls,
        ))
    except Exception as exc:  # noqa: BLE001
        logger.warning("frontier_labs.commit_pushed_failed exc=%r", exc)

    try:
        company_news.commit_pushed(company_news_state_path, published_pending(
            company_news_pending_pushed, publication_candidates["company"],
            summary_urls(company_news_summary),
        ))
    except Exception as exc:  # noqa: BLE001
        logger.warning("company_news.commit_pushed_failed exc=%r", exc)

    try:
        macro_news.commit_pushed(macro_news_state_path, published_pending(
            macro_news_pending_pushed, publication_candidates["macro"],
            summary_urls(macro_news_summary),
        ))
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
