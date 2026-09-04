"""Offline whole-entrypoint checks: no real credentials, network or delivery."""

import importlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.collectors import company_news
from src.config import HOLDINGS
from src.sender.smtp_sender import DeliveryResult
from src.utils.delivery import read_delivery_receipt
from src.utils.runtime_budget import RuntimeBudget
from src.valuation.models import ValuationDisplay


@pytest.mark.parametrize("exhausted", [False, True])
def test_main_sends_controlled_edition_and_does_not_consume_unpublished_news(monkeypatch, tmp_path, exhausted):
    main = importlib.import_module("src.main")
    subject = importlib.import_module("src.processors.subject.generator")
    now = datetime(2026, 9, 4, 0, tzinfo=UTC)
    monkeypatch.setattr(main, "_STATE_DIR", tmp_path)
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
    monkeypatch.setattr(main.stocks, "fetch_all", stock_call)
    monkeypatch.setattr(main, "MorningstarPublicProvider", lambda: MagicMock())
    valuation_calls = []
    def valuations(**kwargs):
        valuation_calls.append(kwargs)
        return {"MSFT": ValuationDisplay(ticker="MSFT", status="current", intrinsic_value=600,
            value_label="公允价值", verified_at=kwargs["checked_at"].isoformat())}, {}
    monkeypatch.setattr(main, "prepare_valuation_displays", valuations)
    item = company_news.NewsItem("Microsoft source never published", now, "https://example.com/msft", "Source")
    pending = {company_news._content_hash("MSFT", item): now.isoformat()}
    monkeypatch.setattr(main.company_news, "fetch_all", lambda *a, **k:
        ([company_news.CompanyNewsBundle(HOLDINGS[0], [item])], pending))
    for collector in (main.macro_news, main.figures, main.frontier_labs):
        monkeypatch.setattr(collector, "fetch_all", lambda *a, **k: ([], {}))
    monkeypatch.setattr(main.buffett_13f, "fetch", lambda **_: (None, None))
    monkeypatch.setattr(main.jiangsu_fuel, "fetch", lambda **_: None)
    monkeypatch.setattr(main.sentiment, "fetch_all", lambda *a, **k: main.sentiment.SentimentBundle([], now))
    monkeypatch.setattr(main, "_translate_all_bundles", lambda **_: None)
    monkeypatch.setattr(main.news_summarizer, "summarize", lambda *a, **k: None)
    monkeypatch.setattr(main.news_summarizer, "generate_silence_note", lambda **_: None)
    monkeypatch.setattr(main.macro_filter, "generate_silence_note", lambda **_: None)
    monkeypatch.setattr(main.figure_filter, "filter_all", lambda *a, **k: [])
    monkeypatch.setattr(main.figure_filter, "generate_silence_note", lambda *a: None)
    monkeypatch.setattr(main.sentiment_judge, "judge", lambda *a, **k: None)
    monkeypatch.setattr(main.frontier_labs_filter, "filter_all_with_status", lambda *a, **k: ([], []))
    monkeypatch.setattr(main.thesis_consolidation, "migrate_history_if_needed", MagicMock(side_effect=RuntimeError("offline")))
    monkeypatch.setattr(main.holdings_intro, "write_intro", lambda *a, **k: None)
    monkeypatch.setattr(main.header_image, "pick_header_image", lambda *a: main.header_image._tier3_local())
    monkeypatch.setattr(main, "_load_logo_assets", lambda *a: ({}, []))
    monkeypatch.setattr(subject, "generate_subject", lambda *a, **k: "测试晨报")
    sent = []
    def send(**kwargs):
        sent.append(kwargs)
        result = DeliveryResult(tuple(kwargs["recipient"]), {})
        kwargs["on_progress"](result)
        assert read_delivery_receipt(tmp_path / "receipt.json")["accepted_count"] == 2
        return result
    monkeypatch.setattr(main, "send_html_email", send)
    assert main.main() == 0
    assert len(sent) == 1
    assert sent[0]["recipient"] == ["a@example.com", "b@example.com"]
    assert "Microsoft source never published" not in sent[0]["html_body"]
    assert company_news._load_pushed_news(tmp_path / "pushed_company_news.json") == (previous if exhausted else {})
    if exhausted:
        for name, content in prior_files.items():
            assert (tmp_path / name).read_bytes() == content
        stock_call.assert_not_called()
        assert "取数超时" in sent[0]["html_body"]
        assert not valuation_calls
    else:
        assert len(valuation_calls) == 2
        assert valuation_calls[-1]["download_original"] is False
