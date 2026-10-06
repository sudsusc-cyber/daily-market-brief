"""Offline whole-entrypoint checks: no real credentials, network or delivery."""

import importlib
import json
import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from bs4 import BeautifulSoup

from src.collectors import company_news
from src.config import HOLDINGS
from src.sender.smtp_sender import DeliveryResult
from src.utils.delivery import read_delivery_receipt
from src.utils.runtime_budget import RuntimeBudget, StageTimeout
from src.valuation.models import ValuationDisplay


@pytest.mark.parametrize("exhausted,scenario", [
    (False, "normal"), (False, "preview"), (True, "normal"), (False, "failed_delivery"),
    (False, "partial_delivery"), (False, "thesis_publication"), (False, "valuation_timeout"),
    (False, "qqqm_retry"), (False, "qqqm_retry_fail"), (False, "qqqm_retry_timeout"),
    (False, "frontier_silent"), (False, "frontier_source"), (False, "frontier_rejected"),
    (False, "frontier_partial"), (False, "frontier_processing"), (False, "frontier_duplicate"),
    (False, "figure_silent"), (False, "figure_source"), (False, "figure_rejected"),
    (False, "figure_partial"), (False, "figure_processing"), (False, "intro_success"),
    (False, "fuel_timeout"), (True, "fuel_budget_exhausted"),
])
def test_main_sends_controlled_edition_and_does_not_consume_unpublished_news(monkeypatch, tmp_path, exhausted, scenario):
    main = importlib.import_module("src.main")
    monkeypatch.setenv("BRIEF_PREVIEW_ONLY", "true" if scenario == "preview" else "false")
    subject = importlib.import_module("src.processors.subject.generator")
    now = datetime(2026, 9, 4, 0, tzinfo=UTC)
    if scenario.startswith("fuel_"):
        now = datetime(2026, 10, 15, 0, tzinfo=UTC)
    monkeypatch.setattr(main, "_STATE_DIR", tmp_path)
    monkeypatch.setenv("BRIEF_AUDIT_DIR", str(tmp_path / "audit"))
    state_files = {
        "pushed_company_news.json": main.company_news,
        "pushed_macro_news.json": main.macro_news,
        "pushed_figures.json": main.figures,
        "pushed_frontier_labs.json": main.frontier_labs,
    }
    previous = {"already-published": now.isoformat()}
    if exhausted:
        for name, collector in state_files.items():
            collector.commit_pushed(tmp_path / name, previous)
    prior_files = {name: (tmp_path / name).read_bytes() for name in state_files} if exhausted else {}
    monkeypatch.setenv("QUALITY_ALERT_PATH", str(tmp_path / "quality.txt"))
    monkeypatch.setenv("DELIVERY_RECEIPT_PATH", str(tmp_path / "receipt.json"))
    monkeypatch.setenv("FORCE_SEND", "true")
    monkeypatch.setattr(main, "now_beijing", lambda: now + timedelta(minutes=1))
    monkeypatch.setattr(main, "load_settings", lambda: SimpleNamespace(
        deepseek_model="fixture", deepseek_api_key="fixture", valuation_enabled=True,
        morningstar_fair_value_enabled=True, finnhub_api_key="fixture", fred_api_key="fixture",
        email_recipient="a@example.com,b@example.com", qq_email_address="sender@example.com",
        qq_email_auth_code="fixture"))
    llm = MagicMock()
    llm.cumulative = SimpleNamespace(input_tokens=0, output_tokens=0, reasoning_tokens=0, cache_hit_tokens=0)
    llm.estimate_cost_cny.return_value = 0.0
    llm.chat.return_value = SimpleNamespace(text=None, error="offline fixture")
    monkeypatch.setattr(main, "LLMClient", lambda **_: llm)
    if exhausted:
        monkeypatch.setattr(main, "RuntimeBudget", lambda: RuntimeBudget(seconds=0))
    stock_call = MagicMock(return_value=[main.stocks.StockSignal(HOLDINGS[0], 500, 400, 300, .25, .667, "NONE")])
    qqqm_calls = []
    if scenario.startswith("qqqm_retry"):
        stock_call.return_value.append(main.stocks.StockSignal(HOLDINGS[-1], 300, 280, 240, .1, .2, "NONE"))
        def qqqm_prepare(**kwargs):
            qqqm_calls.append(kwargs)
            if len(qqqm_calls) == 2 and scenario == "qqqm_retry_timeout":
                raise StageTimeout()
            if len(qqqm_calls) == 2 and scenario == "qqqm_retry":
                return ValuationDisplay("QQQM", "current", 400, financial_as_of="2026-09-03")
            return ValuationDisplay("QQQM", "source_unavailable")
        monkeypatch.setattr(main, "prepare_qqqm_display", qqqm_prepare)
        monkeypatch.setattr(main, "cached_qqqm_display", lambda **_: ValuationDisplay("QQQM", "source_unavailable"))
    monkeypatch.setattr(main.stocks, "fetch_all", stock_call)
    monkeypatch.setattr(main, "MorningstarPublicProvider", lambda: MagicMock())
    valuation_calls = []
    def valuations(**kwargs):
        valuation_calls.append(kwargs)
        if scenario == "valuation_timeout" and len(valuation_calls) == 2:
            raise StageTimeout()
        return {"MSFT": ValuationDisplay(ticker="MSFT", status="current", intrinsic_value=600,
            value_label="公允价值", financial_as_of="2026-09-01",
            verified_at=kwargs["checked_at"].isoformat())}, {}
    monkeypatch.setattr(main, "prepare_valuation_displays", valuations)
    if scenario == "valuation_timeout":
        monkeypatch.setattr(main, "cached_morningstar_displays", lambda **_: {
            "MSFT": ValuationDisplay("MSFT", "not_due", 650, value_label="公允价值",
                financial_as_of="2026-09-04", verified_at=now.isoformat())})
    item = company_news.NewsItem("Microsoft source never published", now, "https://example.com/msft", "Source")
    pending = {company_news._content_hash("MSFT", item): now.isoformat()}
    monkeypatch.setattr(main.company_news, "fetch_all", lambda *a, **k:
        ([company_news.CompanyNewsBundle(HOLDINGS[0], [item])], pending))
    for collector in (main.macro_news, main.figures, main.frontier_labs):
        monkeypatch.setattr(collector, "fetch_all", lambda *a, **k: ([], {}))
    if scenario.startswith("frontier_"):
        def frontier_item(title, suffix):
            return main.frontier_labs.FrontierItem(
                "OpenAI", title, "", now, f"https://example.com/{suffix}", "Reuters",
                "google_news", ["MSFT"])
        good = frontier_item("OpenAI 宣布新的企业云合作。", "good")
        bad = frontier_item("OpenAI announces a cloud agreement", "bad")
        items = [good, bad] if scenario == "frontier_partial" else [good] if scenario == "frontier_duplicate" else [bad]
        errors = ["RSS timeout"] if scenario == "frontier_source" else []
        if scenario == "frontier_source":
            items = []
        bundles = [main.frontier_labs.FrontierBundle("OpenAI", ["MSFT"], items, errors)]
        frontier_pending = {main.frontier_labs._content_hash("OpenAI", row): now.isoformat() for row in items}
        monkeypatch.setattr(main.frontier_labs, "fetch_all", lambda *a, **k: (bundles, frontier_pending))
        output = "\n".join(
            f"▦ {i}: yes | score=5 | tickers=MSFT | OpenAI 宣布新的企业云合作。"
            for i in range(1, len(items) + 1))
        if scenario == "frontier_silent":
            output = "▦ 1: no | score=2 | 普通更新"
        if scenario == "frontier_processing":
            output = None
        llm.chat.return_value = SimpleNamespace(text=output, error="timeout")
    if scenario == "frontier_duplicate":
        from src.processors.source_grounding import grounded_text
        macro_item = main.macro_news.MacroNewsItem(good.title, now, "https://example.com/macro", "Reuters")
        macro_bundles = [main.macro_news.MacroFeedBundle("Reuters", [macro_item])]
        monkeypatch.setattr(main.macro_news, "fetch_all", lambda *a, **k: (macro_bundles, {}))
        text, evidence = grounded_text(good.title, [macro_item])
        summary = main.macro_filter.MacroNewsSummary(text, [main.macro_filter.Footnote(1, macro_item.url, "Reuters")], evidence)
        monkeypatch.setattr(main.macro_filter, "summarize", lambda *a, **k: summary)
    if scenario.startswith("figure_"):
        good = main.figures.FigureMention("黄仁勋明确表示将投资100亿美元建设数据中心。", "", now,
                                          "https://example.com/good", "Reuters")
        bad = main.figures.FigureMention("Jensen said a cloud deal was announced", "", now,
                                         "https://example.com/bad", "Reuters")
        items = [good, bad] if scenario == "figure_partial" else [bad]
        error = "RSS timeout" if scenario == "figure_source" else None
        if error:
            items = []
        bundles = [main.figures.FigureBundle("黄仁勋", "Jensen Huang", "Jensen Huang", items, error)]
        pending_figures = {main.figures._content_hash("黄仁勋", row): now.isoformat() for row in items}
        monkeypatch.setattr(main.figures, "fetch_all", lambda *a, **k: (bundles, pending_figures))
        output = "\n".join(f"▦ {i}: yes | score=5 | {good.title}" for i in range(1, len(items) + 1))
        if scenario == "figure_silent":
            output = "▦ 1: no | score=2 | 普通更新"
        if scenario == "figure_processing":
            output = None
        llm.chat.return_value = SimpleNamespace(text=output, error="timeout")
    monkeypatch.setattr(main.buffett_13f, "fetch", lambda **_: (None, None))
    monkeypatch.setattr(main.jiangsu_fuel, "fetch", lambda **_: None)
    if scenario == "fuel_timeout":
        def fuel_timeout(**kwargs):
            kwargs["on_schedule"](main.jiangsu_fuel.schedule_only_alert(today=kwargs["today"]))
            raise StageTimeout()
        monkeypatch.setattr(main.jiangsu_fuel, "fetch", fuel_timeout)
    monkeypatch.setattr(main.sentiment, "fetch_all", lambda *a, **k: main.sentiment.SentimentBundle([], now))
    monkeypatch.setattr(main, "_translate_all_bundles", lambda **_: None)
    monkeypatch.setattr(main.news_summarizer, "summarize", lambda *a, **k: None)
    monkeypatch.setattr(main.news_summarizer, "generate_silence_note", lambda **_: None)
    monkeypatch.setattr(main.macro_filter, "generate_silence_note", lambda **_: None)
    if not scenario.startswith("figure_"):
        monkeypatch.setattr(main.figure_filter, "filter_all", lambda *a, **k: [])
    monkeypatch.setattr(main.figure_filter, "generate_silence_note", lambda *a: None)
    monkeypatch.setattr(main.sentiment_judge, "judge", lambda *a, **k: None)
    publication_scenario = scenario in {"failed_delivery", "partial_delivery", "thesis_publication", "preview"}
    if publication_scenario:
        fact = "微软计划投资100亿美元建设云基础设施。"
        row = dict(original_title=fact, original_summary="", excerpt=fact, output_text=fact,
                   validated_text=fact, mode="source_extract", url="https://example.com/verified",
                   published_at="2026-09-04", source_name="Reuters")
        summary = SimpleNamespace(summary_html=fact, evidence=[row],
                                  footnotes=[SimpleNamespace(index=1, url=row["url"], source="Reuters")])
        monkeypatch.setattr(main.news_summarizer, "summarize", lambda *a, **k: summary)
    intro = "持仓尚未出现既定买入信号。思想的分量，要在时间中慢慢衡量。" if scenario in {"intro_success", "failed_delivery", "preview"} else None
    monkeypatch.setattr(main.holdings_intro, "write_intro", lambda *a, **k: intro)
    monkeypatch.setattr(main.header_image, "pick_header_image", lambda *a: main.header_image._tier3_local())
    monkeypatch.setattr(main, "_load_logo_assets", lambda *a: ({}, []))
    monkeypatch.setattr(subject, "generate_subject", lambda *a, **k: "测试晨报")
    sent = []
    def send(**kwargs):
        sent.append(kwargs)
        if intro:
            assert not (tmp_path / "published_editorial.json").exists()
        if publication_scenario:
            assert not main.thesis_renderer.load_publications(tmp_path)
        if scenario == "failed_delivery":
            raise RuntimeError("controlled SMTP failure")
        result = DeliveryResult(tuple(kwargs["recipient"]), {})
        if scenario == "partial_delivery":
            result = DeliveryResult(("a@example.com",), {"b@example.com": (550, b"rejected")})
        kwargs["on_progress"](result)
        assert read_delivery_receipt(tmp_path / "receipt.json")["accepted_count"] == len(result.accepted)
        return result
    monkeypatch.setattr(main, "send_html_email", send)
    if scenario == "failed_delivery":
        with pytest.raises(RuntimeError, match="controlled SMTP failure"):
            main.main()
        assert not main.thesis_renderer.load_publications(tmp_path)
        assert not (tmp_path / "pushed_company_news.json").exists()
        assert not (tmp_path / "published_editorial.json").exists()
        return
    assert main.main() == 0
    manifest = json.loads(next((tmp_path / "audit").glob("*/manifest.json")).read_text())
    assert manifest["content"]["status"] == "degraded"
    health = manifest["content"]["section_health"]
    assert health["sentiment"]["fallback"] is True
    assert health["holdings_intro"]["fallback"] is (intro is None)
    if scenario == "intro_success":
        history_rows = json.loads((tmp_path / "published_editorial.json").read_text())
        assert any(r["section"] == "holdings_intro" and r["text"] == intro for r in history_rows)
    if not exhausted and not publication_scenario:
        assert health["company"]["processing_failures"] == 1
    if exhausted:
        assert health["collection"]["timeout_count"] > 0
        assert health["company"]["source_failures"] > 0
        assert health["macro"]["source_failures"] > 0
    if scenario == "preview":
        assert not sent
        assert not (tmp_path / "published_editorial.json").exists()
        assert not main.thesis_renderer.load_publications(tmp_path)
        assert not (tmp_path / "receipt.json").exists()
        assert not (tmp_path / "pushed_company_news.json").exists()
        manifests = list((tmp_path / "audit").glob("*/manifest.json"))
        assert len(manifests) == 1
        assert json.loads(manifests[0].read_text())["delivery"]["status"] == "not_sent"
        assert (manifests[0].parent / "preview.html").exists()
        return
    assert len(sent) == 1
    if scenario.startswith("fuel_"):
        body = sent[0]["html_body"]
        assert "油价预告" in body and "10 月 15 日 24 时" in body
        assert "涨跌方向与幅度待更新" in body
        assert "模型代理" not in body and "媒体预测" not in body
        assert health["fuel"]["fallback"]
        fuel_audit = manifest["content"]["section_details"]["fuel"]
        assert fuel_audit["adjustment_date"] == "2026-10-15"
        assert fuel_audit["forecast_method"] == "schedule_only"
        assert fuel_audit["observed_at"] is fuel_audit["fetched_at"] is None
        assert "油价" in manifest["content"]["section_details"]["stage_timeouts"]
        assert "油价预告方向降级" in (tmp_path / "quality.txt").read_text()
    if scenario.startswith("frontier_"):
        expected = {"frontier_silent": "silent", "frontier_source": "source_unavailable",
                    "frontier_rejected": "content_rejected", "frontier_partial": "partial",
                    "frontier_processing": "processing_failed", "frontier_duplicate": "silent"}[scenario]
        assert health["frontier"]["state"] == expected
        body = sent[0]["html_body"]
        assert "前沿动态整理未完成" not in body
        if expected == "silent":
            assert "前沿动态" not in body
            assert health["frontier"]["silence"]
        elif expected == "partial":
            assert "宣布新的企业云合作。" in body
            assert "本期暂不刊载" not in body
            assert health["frontier"]["content_rejections"] == 1
            assert manifest["content"]["diagnostics"]["frontier"]["content_rejections"]
            assert len(manifest["content"]["summary_mapping"]["frontier"]) == 1
        else:
            assert "本期暂不刊载" in body
            assert not health["frontier"]["silence"]
        saved_frontier = main.frontier_labs._load_pushed(tmp_path / "pushed_frontier_labs.json")
        assert set(saved_frontier) == (
            {main.frontier_labs._content_hash("OpenAI", good)} if expected == "partial" or scenario == "frontier_duplicate" else set())
    if scenario == "frontier_duplicate":
        assert len(manifest["content"]["summary_mapping"]["macro"]) == 2
        assert manifest["content"]["diagnostics"]["frontier"]["merged_into_macro"] == [good.url]
        assert sent[0]["html_body"].count("宣布新的企业云合作。") == 1
        assert good.url in sent[0]["html_body"] and macro_item.url in sent[0]["html_body"]
    if scenario.startswith("figure_"):
        body = sent[0]["html_body"]
        figures_health = health["figures"]
        assert figures_health["processing_failures"] == int(scenario == "figure_processing")
        assert figures_health["content_rejections"] == 0
        assert figures_health["translation_failures"] == int(scenario in {"figure_rejected", "figure_partial"})
        assert figures_health["source_failures"] == int(scenario == "figure_source")
        assert figures_health["silence"] == (scenario == "figure_silent")
        assert "关键发言整理未完成" not in body
        if scenario in {"figure_partial", "figure_rejected", "figure_silent"}:
            audit = manifest["content"]["diagnostics"]["figure_verification"]["黄仁勋"]
            assert audit and audit[0]["decisions"] and audit[0]["candidates"]
            assert audit[0]["candidates"][0]["title"]
            if scenario in {"figure_partial", "figure_rejected"}:
                assert audit[0]["rejected_indexes"]
            # Internal source diagnostics must not leak into the email body.
            assert "translation_recovery" not in body and "rejected_indexes" not in body
        if scenario == "figure_partial":
            assert "投资100亿美元建设数据中心。" in re.sub(r"\s", "", BeautifulSoup(body, "html.parser").get_text())
            assert not figures_health["fallback"]
            assert manifest["content"]["diagnostics"]["figures"]
            assert len(manifest["content"]["summary_mapping"]["figures"]) == 1
        elif scenario == "figure_rejected":
            assert "关键发言翻译处理未完成" in body
            assert "关键发言候选内容未通过核验" not in body
            assert manifest["content"]["diagnostics"]["figure_failure_kinds"]["黄仁勋"] == "translation"
        elif scenario != "figure_silent":
            assert "本期暂不刊载" in body
        saved = main.figures._load_pushed(tmp_path / "pushed_figures.json")
        assert set(saved) == ({main.figures._content_hash("黄仁勋", good)} if scenario == "figure_partial" else set())
    if scenario.startswith("qqqm_retry"):
        assert len(qqqm_calls) == 2
        assert valuation_calls[-1]["qqqm_display"].status == ("current" if scenario == "qqqm_retry" else "source_unavailable")
    assert sent[0]["recipient"] == ["a@example.com", "b@example.com"]
    assert "Microsoft source never published" not in sent[0]["html_body"]
    assert company_news._load_pushed_news(tmp_path / "pushed_company_news.json") == (previous if exhausted else {})
    if publication_scenario:
        assert len(main.thesis_renderer.load_publications(tmp_path)) == 1
        assert "基础设施投入的长期价值取决于资本回报" in sent[0]["html_body"]
        manifest = json.loads(next((tmp_path / "audit").glob("*/manifest.json")).read_text())
        assert len(manifest["content"]["judgment_mapping"]) == 1
        assert manifest["content"]["judgment_mapping"][0]["evidence"]["excerpt"] == fact
    if scenario == "valuation_timeout":
        assert '>650.00</div>' in sent[0]["html_body"]
        assert '>600.00</div>' not in sent[0]["html_body"]
    if exhausted:
        for name, content in prior_files.items():
            assert (tmp_path / name).read_bytes() == content
        stock_call.assert_not_called()
        assert "取数超时" in sent[0]["html_body"]
        assert not valuation_calls
    else:
        assert len(valuation_calls) == 2
        assert valuation_calls[-1]["download_original"] is False
