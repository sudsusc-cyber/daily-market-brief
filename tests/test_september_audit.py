"""Reproducible audit counterexamples; all IO stays in fixtures/temp directories."""

import json
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest

from scripts import monitor
from src.collectors import sentiment, stocks
from src.config import HOLDINGS
from src.main import _valuation_recency, _verified_timestamp
from src.processors import llm_client
from src.processors.editorial_history import similar
from src.processors.sentiment_judge import _score_metric, score_sentiment
from src.processors.thesis import consolidation, rules, state
from src.processors.thesis.models import ThesisEvidence, ThesisState
from src.renderer.render import _filter_price
from src.sender.smtp_sender import DeliveryResult, _html_to_plain
from src.utils.last_good import LastGoodCache
from src.utils.market_clock import calendar, validate_history
from src.utils.runtime_budget import RuntimeBudget, StageTimeout
from src.utils.secrets import redact_secrets
from src.valuation.models import ValuationDisplay

TODAY = date(2026, 9, 5)
NOW = datetime(2026, 9, 5, 0, tzinfo=UTC)


@pytest.mark.parametrize("saved", ["2026-09-06", "2099-01-01", "bad", None, []])
def test_future_or_invalid_cache_not_reused(saved):
    assert LastGoodCache.is_stale(saved, today=TODAY)


def test_future_sentiment_cache_cannot_enter_score(tmp_path):
    cache = LastGoodCache(tmp_path)
    cache.put("sentiment.VIX", {"current": 10}, today=date(2099, 1, 1))
    result = sentiment._with_last_good(
        lambda: sentiment.SentimentMetric("VIX", None, None, None, error="offline"),
        cache=cache, cache_key="VIX", today=TODAY,
    )
    assert result.current is None
    assert result.error


@pytest.mark.parametrize("root", [[], None, 0, "bad"])
def test_bad_thesis_root_isolated(tmp_path, root):
    (tmp_path / "thesis_state.json").write_text(json.dumps(root))
    assert state.load_state(tmp_path) == {}
    (tmp_path / "thesis_theme_migration.json").write_text(json.dumps(root))
    assert not consolidation._migration_already_applied(tmp_path)


def evidence(day="2026-09-05", eid="valid"):
    return ThesisEvidence(eid, day, "company_news", "Reuters", "https://example.com/a",
        ["MSFT"], "cloud", "support", 5, "multi_year", "Cloud evidence", "Long-term relevance")


def test_thesis_bad_future_rows_do_not_hide_good_row(tmp_path):
    good = asdict(evidence())
    bad = {**good, "strength": [], "evidence_id": "valid"}
    rows = [[], None, 0, {**good, "date": "banana"}, {**good, "date": "2099-01-01"},
            {**good, "date": None}, bad, good]
    path = tmp_path / "thesis_evidence_2026.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in rows))
    assert [e.evidence_id for e in state.load_recent_evidence(tmp_path, today=TODAY)] == ["valid"]
    assert state.append_evidence([evidence(eid="new")], tmp_path, today=TODAY) == 1


def test_new_thesis_directory_can_be_written(tmp_path):
    nested = tmp_path / "new" / "state"
    assert state.append_evidence([evidence()], nested, today=TODAY) == 1
    state.save_state({}, nested)
    assert state.load_recent_evidence(nested, today=TODAY)


def test_candidate_thesis_is_not_marked_published():
    st = ThesisState(theme="cloud", status="core", cadence="quarterly", stale_after_days=180,
        related_tickers=["MSFT"],
        first_seen="2026-01-01", last_evidence_date="2026-09-04",
        last_strong_evidence_date="2026-09-04", one_line_thesis="Cloud thesis")
    updated, events = rules.run_state_transitions(TODAY, {"cloud": st}, [evidence()])
    assert events
    assert updated["cloud"].last_displayed_date is None


def client_for(monkeypatch, response):
    api = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **_: response)),
                          responses=SimpleNamespace(create=lambda **_: response))
    monkeypatch.setattr(llm_client, "OpenAI", lambda **_: api)
    return llm_client.LLMClient(api_key="fixture")


@pytest.mark.parametrize("finish", ["length", "content_filter", "tool_calls"])
def test_partial_chat_discarded_but_usage_counted(monkeypatch, finish):
    client = client_for(monkeypatch, SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="看起来完整但实际上已截断"), finish_reason=finish)],
        usage=SimpleNamespace(prompt_tokens=100, completion_tokens=10)))
    result = client.chat("fixture")
    assert result.text is None and "IncompleteOutput" in result.error
    assert client.cumulative.input_tokens == 100


@pytest.mark.parametrize("status", ["incomplete", "failed", "cancelled", "in_progress"])
def test_partial_search_not_accepted(monkeypatch, status):
    client = client_for(monkeypatch, SimpleNamespace(status=status, output_text='{"value":600}',
        output=[SimpleNamespace(type="web_search_call")], usage=None))
    result = client.search_web("fixture", allowed_domains=("example.com",), market_data=True)
    assert result.text is None and "IncompleteOutput" in result.error


def test_malformed_chat_or_accounting_does_not_raise(monkeypatch):
    response = SimpleNamespace(choices=[SimpleNamespace(message=None)],
        usage=SimpleNamespace(prompt_tokens="bad", completion_tokens=float("inf")))
    result = client_for(monkeypatch, response).chat("fixture")
    assert result.text is None and "MalformedOutput" in result.error
    assert result.usage.input_tokens == 0


def test_hole_in_daily_or_weekly_history_rejected():
    cal = calendar("MSFT", 2026)
    days = cal.sessions_in_range("2025-07-01", "2026-09-04")
    assert validate_history(days, symbol="MSFT", interval="1d", now=NOW)
    with pytest.raises(ValueError, match="缺失"):
        validate_history(days.delete(100), symbol="MSFT", interval="1d", now=NOW)
    weeks = pd.date_range(end="2026-08-31", periods=210, freq="W-MON")
    assert validate_history(weeks, symbol="MSFT", interval="1wk", now=NOW)
    with pytest.raises(ValueError, match="缺失"):
        validate_history(weeks.delete(100), symbol="MSFT", interval="1wk", now=NOW)


def test_irrelevant_old_history_gap_does_not_block_valid_strategy_window():
    weeks = pd.date_range(end="2026-08-31", periods=210, freq="W-MON")
    assert stocks._validate_history_window(weeks.delete(5), symbol="MSFT", interval="1wk", now=NOW)
    assert stocks._validate_history_window(weeks.delete(50), symbol="NVDA", interval="1wk", now=NOW)
    with pytest.raises(ValueError, match="缺失"):
        stocks._validate_history_window(weeks.delete(150), symbol="NVDA", interval="1wk", now=NOW)


def test_collected_stocks_survive_later_timeout(monkeypatch):
    good = stocks.StockSignal(HOLDINGS[0], 500, 400, 300, .25, .667, "NONE")
    monkeypatch.setattr(stocks, "fetch_one", Mock(side_effect=[good, StageTimeout()]))
    completed = []
    result = RuntimeBudget().call(stocks.fetch_all, HOLDINGS[:2], seconds=1,
        fallback=lambda: completed, on_result=completed.append)
    assert result == [good]


def test_monitor_partial_acceptance_fails(monkeypatch):
    monkeypatch.setattr(monitor, "load_email_settings", lambda: SimpleNamespace(
        email_recipient="a@example.com,b@example.com", qq_email_address="s@example.com", qq_email_auth_code="fixture"))
    monkeypatch.setattr(monitor, "send_html_email", lambda **_: DeliveryResult(
        ("a@example.com",), {"b@example.com": (550, b"rejected")}))
    with pytest.raises(RuntimeError, match="accepted=1 refused=1"):
        monitor.send_alert("fixture")


@pytest.mark.parametrize("value", [True, False, float("nan"), float("inf"), -10, 0, "600"])
def test_invalid_valuation_cannot_be_attractive(value):
    view = ValuationDisplay("MSFT", "current", intrinsic_value=value, implied_return=.2, hurdle_rate=.1)
    assert view.is_pending and not view.is_attractive


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), -10, 0])
def test_invalid_valuation_is_not_rendered_as_a_number(value):
    from bs4 import BeautifulSoup

    from scripts.preview_email import _build_mock_signals
    from src.renderer.render import render_email

    html = render_email(signals=_build_mock_signals()[:1], generated_at=NOW,
        valuations={"MSFT": ValuationDisplay("MSFT", "current", value, .2, .1)})
    soup = BeautifulSoup(html, "html.parser")
    assert not soup.select(".holding-valuation-main, .holding-implied-return")
    assert "公允价值未核验：MSFT" in soup.get_text()


@pytest.mark.parametrize("value", [True, float("inf"), float("nan"), 10**400])
def test_invalid_return_cannot_be_red(value):
    assert not ValuationDisplay("MSFT", "current", 600, value, .1).is_attractive
    assert _filter_price(value) == "—"


@pytest.mark.parametrize("name,value", [("VIX", -1), ("VIX", True), ("CNN Fear & Greed", 101),
                                         ("CNN Fear & Greed", -1), ("DXY", 0)])
def test_invalid_metric_does_not_turn_into_extreme_sentiment(name, value):
    assert _score_metric(name, value) is None


def test_duplicate_core_metric_cannot_satisfy_coverage():
    metric = sentiment.SentimentMetric("CNN Fear & Greed", 60, 59, None)
    assert score_sentiment(sentiment.SentimentBundle([metric, metric], NOW)) is None


def test_newer_verified_report_wins_over_first_pass():
    old = ValuationDisplay("MSFT", "current", 600, financial_as_of="2026-09-01", verified_at=NOW.isoformat())
    new = ValuationDisplay("MSFT", "not_due", 650, financial_as_of="2026-09-04", verified_at=NOW.isoformat())
    assert _valuation_recency(new) > _valuation_recency(old)
    assert _verified_timestamp("invalid") is None
    assert _verified_timestamp("2026-09-05") is None


def test_opposite_news_fact_is_not_a_duplicate():
    assert not similar("微软的大型收购交易已获批准，涉及云业务未来长期发展和全球资本配置安排",
                       "微软的大型收购交易未获批准，涉及云业务未来长期发展和全球资本配置安排")


@pytest.mark.parametrize("text", ["Authorization: Bearer testSECRET", "Authorization: Basic testSECRET",
                                   "{'api_key': 'testSECRET'}", 'https://u:testSECRET@example.com/a'])
def test_header_and_structured_credentials_redacted(text):
    assert "testSECRET" not in redact_secrets(text)


def test_workflow_production_identity_and_retry_cache_keys():
    root = Path(__file__).parents[1] / ".github" / "workflows"
    for name in ("daily.yml", "formal-test-send.yml"):
        text = (root / name).read_text()
        assert "if: github.ref == 'refs/heads/main'" in text
        assert "DEEPSEEK_MODEL: ${{ vars.DEEPSEEK_MODEL || 'deepseek-v4-flash' }}" in text
    assert (root / "daily.yml").read_text().count("key: daily-state-${{ github.run_id }}-${{ github.run_attempt }}") == 2


def test_plain_mail_omits_css_scripts_and_keeps_cell_boundaries():
    html = '<html><head><title>hidden title</title><style>.x{color:red}</style></head><body><p>晨报</p><table><tr><td>MSFT</td><td>600</td></tr></table><script>hidden js</script><p>来源[1]</p></body></html>'
    assert _html_to_plain(html) == '晨报\nMSFT 600\n来源[1]'


def test_invalid_duplicate_cannot_block_repaired_thesis_evidence(tmp_path):
    bad = {**asdict(evidence()), "strength": []}
    (tmp_path / "thesis_evidence_2026.jsonl").write_text(json.dumps(bad))
    assert state.append_evidence([evidence()], tmp_path, today=TODAY) == 1
    assert state.load_recent_evidence(tmp_path, today=TODAY) == [evidence()]


def test_unreadable_thesis_ledger_is_not_overwritten(monkeypatch, tmp_path):
    ledger = tmp_path / "thesis_evidence_2026.jsonl"
    ledger.write_text(json.dumps(asdict(evidence())))
    original = ledger.read_bytes()
    monkeypatch.setattr(Path, "read_text", Mock(side_effect=OSError("unreadable")))
    with pytest.raises(OSError):
        state.append_evidence([evidence(eid="new")], tmp_path, today=TODAY)
    assert ledger.read_bytes() == original


@pytest.mark.parametrize("direction", ["support", "risk", "neutral", "new_variable"])
def test_all_documented_thesis_directions_survive_ledger(direction):
    assert state._dict_to_evidence({**asdict(evidence()), "direction": direction}).direction == direction


@pytest.mark.parametrize("scenario", ["valid", "oversize_bytes", "oversize_pixels", "redirect", "non_image", "wrong_host"])
def test_external_report_image_is_bounded(monkeypatch, scenario):
    from io import BytesIO

    from PIL import Image

    from src.valuation import yahoo_morningstar as yahoo

    output = BytesIO()
    Image.new("RGB", (10, 10), "white").save(output, format="PNG")
    data = b"<html>not an image</html>" if scenario == "non_image" else output.getvalue()
    response = SimpleNamespace(status_code=302 if scenario == "redirect" else 200,
        raise_for_status=lambda: None, iter_content=lambda **_: iter([data]), close=Mock())
    session = SimpleNamespace(headers={}, get=Mock(return_value=response))
    if scenario == "oversize_bytes":
        monkeypatch.setattr(yahoo, "_MAX_SNAPSHOT_BYTES", 10)
    if scenario == "oversize_pixels":
        monkeypatch.setattr(yahoo, "_MAX_SNAPSHOT_PIXELS", 10)
    provider = yahoo.YahooMorningstarProvider(session=session)
    url = "https://evil.test/report.png" if scenario == "wrong_host" else "https://s.yimg.com/report.png"
    if scenario == "valid":
        with provider._snapshot_image(url) as image:
            assert image.size == (10, 10)
    else:
        with pytest.raises((ValueError, OSError)):
            provider._snapshot_image(url)
    if scenario == "wrong_host":
        session.get.assert_not_called()
    else:
        response.close.assert_called_once()
        assert session.get.call_args.kwargs["stream"] is True
        assert session.get.call_args.kwargs["allow_redirects"] is False
