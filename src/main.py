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
from datetime import UTC
from pathlib import Path

from src.collectors import (
    buffett_13f,
    company_news,
    figures,
    header_image,
    macro_news,
    sentiment,
    stocks,
    xueqiu_duan,
)
from src.config import HOLDINGS, Holding
from src.processors import (
    duan_filter,
    figure_filter,
    holdings_intro,
    macro_filter,
    news_summarizer,
    sentiment_judge,
    translator,
)
from src.processors.figure_filter import FigureKeyPoint, FigureSummary
from src.processors.llm_client import LLMClient
from src.renderer.render import render_email
from src.sender.smtp_sender import InlineImage, send_html_email
from src.settings import load_settings
from src.utils.dates import now_beijing

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_LOGOS_DIR = _PROJECT_ROOT / "assets" / "logos"
_STATE_DIR = _PROJECT_ROOT / "state"
# 段永平模块独立状态目录(.state),与既有 state/ 完全隔离,保证纯增量
_DUAN_STATE_DIR = _PROJECT_ROOT / ".state"
_BUILD_DIR = _PROJECT_ROOT / "build"
_VOICES_JSON = _BUILD_DIR / "voices.json"


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


def _duan_summary_from_quotes(quotes: list[xueqiu_duan.DuanQuote]) -> FigureSummary:
    """段永平雪球短文 → FigureSummary,完全绕过 figure_filter LLM 通道。

    text 字段直接用 collector 输出的纯文本(已 HTML 清洗 + 截断 + 长度过滤),
    没有任何 LLM 改写步骤,确保「关键发言」段呈现的是雪球原话。
    source_url 指向原帖,模板渲染时会以脚注 [N] 形式给出 —— 与黄/巴/但等量齐观。
    回复型帖子的 parent_text/parent_author 透传给 FigureKeyPoint,模板自动渲染
    在段永平正文之上,防止读者断章取义。
    """
    items = [
        FigureKeyPoint(
            text=q.text,
            source_url=q.url,
            source_name="雪球",
            parent_text=q.parent_text,
            parent_author=q.parent_author,
        )
        for q in quotes
    ]
    return FigureSummary(person="段永平", person_en="Duan Yongping", items=items)


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

    # ---------- 节假日预检(M6;cron 仍按周二-周六触发,但美股节假日要跳过) ----------
    import os

    from src.utils.holidays import should_send_today
    if os.environ.get("FORCE_SEND", "").strip().lower() not in ("1", "true"):
        ok, reason = should_send_today(now_bj.date())
        logger.info("holidays.check ok=%s reason=%s", ok, reason)
        if not ok:
            logger.info("main.skipped reason=%s", reason)
            return 0

    # ---------- 幂等性预检(双 cron 触发时,后触发的若发现今天已发过 → 跳过) ----------
    if os.environ.get("FORCE_SEND", "").strip().lower() not in ("1", "true"):
        from src.utils.idempotency import already_sent_today
        if already_sent_today():
            logger.info("main.skipped reason=今日已通过另一次 cron 成功发送,跳过双触发")
            return 0

    # ---------- 数据采集(M2 / M3) ----------
    logger.info("collect.stocks count=%d", len(HOLDINGS))
    signals = stocks.fetch_all(HOLDINGS)

    logger.info("collect.company_news")
    cn_bundles = company_news.fetch_all(HOLDINGS, settings.finnhub_api_key)

    logger.info("collect.macro_news")
    macro_bundles = macro_news.fetch_all()

    logger.info("collect.figures")
    fig_bundles = figures.fetch_all(state_path=_STATE_DIR / "pushed_figures.json")

    # 段永平雪球抓取(纯增量模块):
    # - 异常一律吞掉,绝不阻断邮件发送
    # - 状态更新延迟到邮件发送成功之后(见文末 commit_state 调用)
    # - duan_fetch.quotes 是全部 parsed,用于推进 last_seen(避免被判 no 的帖子下次重判)
    # - duan_quotes_relevant 见后文 LLM 相关性筛选,只它进 voices.json 与「关键发言」段
    # - duan_fetch 也用于 update_health(),把抓取健康状态持久化到 .state/duan_health.json
    logger.info("collect.xueqiu_duan")
    duan_uid = (os.environ.get("DUAN_USER_ID") or "").strip()
    duan_token = (os.environ.get("XQ_A_TOKEN") or "").strip() or None
    try:
        duan_fetch = xueqiu_duan.fetch_new_quotes(
            uid=duan_uid, token=duan_token, state_dir=_DUAN_STATE_DIR,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("xueqiu_duan.unexpected exc=%r 视为空,继续主流程", exc)
        duan_fetch = xueqiu_duan.FetchResult(
            quotes=[], api_failed=True, error=f"{type(exc).__name__}: {exc}",
        )
    duan_quotes_all = duan_fetch.quotes
    # 健康状态立即更新(邮件是否成功不影响"抓取是否成功"的事实)
    try:
        duan_health = xueqiu_duan.update_health(_DUAN_STATE_DIR, duan_fetch)
    except Exception as exc:  # noqa: BLE001
        logger.warning("xueqiu_duan.health_update_failed exc=%r", exc)
        duan_health = xueqiu_duan.HealthState()
    duan_alerts = xueqiu_duan.compute_alerts(duan_health)
    if duan_alerts:
        for a in duan_alerts:
            logger.warning("xueqiu_duan.alert %s", a)
    logger.info(
        "xueqiu_duan.collected count=%d api_failed=%s empty_streak=%d",
        len(duan_quotes_all), duan_fetch.api_failed,
        duan_health.consecutive_empty_returns,
    )

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
    # M5.10 质量门槛:无 items 的人物(规则层全砍 / LLM 全 no)整个不渲染
    _before = len(figure_summaries)
    figure_summaries = [f for f in figure_summaries if f.items]
    if len(figure_summaries) < _before:
        logger.info("figure_filter.dropped_silent count=%d", _before - len(figure_summaries))
    # 段永平相关性筛选:LLM 仅做 yes/no 判定,绝不改写段永平正文。
    # - 只投资 / 公司 / 商业 / 行业相关条目保留
    # - 日常闲聊(遛狗 / 打球 / 家事)一律丢弃
    # - LLM 失败时 fail-safe 返回空,章节走原占位逻辑
    duan_quotes_relevant: list[xueqiu_duan.DuanQuote] = []
    if duan_quotes_all:
        try:
            duan_quotes_relevant = duan_filter.judge_relevance(duan_quotes_all, client=llm)
        except Exception as exc:  # noqa: BLE001
            logger.warning("duan_filter.unexpected exc=%r 视为空,主流程继续", exc)
            duan_quotes_relevant = []
    # 原帖上下文 LLM 压缩:对回复型帖子,把 parent_text 替换成 ≤30 字的极短摘要,
    # 避免占用「关键发言」过多版面。段永平本人的 .text 字段不动,保持原文。
    if duan_quotes_relevant:
        try:
            duan_quotes_relevant = duan_filter.summarize_parents(
                duan_quotes_relevant, client=llm,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("duan_filter.parent_summary_unexpected exc=%r 走原 parent_text", exc)
    # voices.json 写入"展示用"的相关条目(便于诊断邮件实际呈现内容)
    try:
        xueqiu_duan.write_voices_json(_VOICES_JSON, duan_quotes_relevant)
    except Exception as exc:  # noqa: BLE001
        logger.warning("xueqiu_duan.voices_write_failed exc=%r", exc)
    # 注入「关键发言」段:仅相关条目;为空时章节走原沉默 / 其他人物逻辑,完全不受影响
    if duan_quotes_relevant:
        figure_summaries.append(_duan_summary_from_quotes(duan_quotes_relevant))
        logger.info(
            "xueqiu_duan.injected items=%d (filtered from %d)",
            len(duan_quotes_relevant), len(duan_quotes_all),
        )
    # 全员沉默时:LLM 写一句古典韵味的占位语
    figure_silence_note = None
    figure_footnotes = []
    if not figure_summaries:
        figure_silence_note = figure_filter.generate_silence_note(llm)
    else:
        # 跨人物统一编号 [1] [2] ...,章节底部一次列出所有来源
        figure_footnotes = figure_filter.assign_footnotes(figure_summaries)

    logger.info("processors.sentiment_judge")
    sentiment_verdict = sentiment_judge.judge(sentiment_bundle, client=llm)

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
        buffett_13f=buffett_bundle,
        system_alerts=duan_alerts,
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
    subject = generate_subject(subject_data, llm=llm, today_bj=now_bj.date())

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

    # ---------- 段永平状态提交(仅在邮件发送成功后) ----------
    # 关键:用 duan_quotes_all(全部 parsed)推进 last_seen,而非 relevant 子集 ——
    # 否则被相关性筛选丢弃的帖子下次还会再被判一次,白烧 token
    try:
        from datetime import datetime as _dt
        xueqiu_duan.commit_state(
            _DUAN_STATE_DIR,
            duan_quotes_all,
            sent_at=_dt.now(tz=UTC),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("xueqiu_duan.commit_state_failed exc=%r", exc)

    logger.info("main.done  est_cost=¥%.4f", cost_cny)
    return 0


if __name__ == "__main__":
    sys.exit(main())
