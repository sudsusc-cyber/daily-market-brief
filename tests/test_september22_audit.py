"""Offline counterexamples from the Sept 22 audit, not claims about run #230."""

import copy
import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest
from bs4 import BeautifulSoup

from scripts import monitor
from src.collectors import company_news, jiangsu_fuel, macro_news, sentiment, stocks
from src.processors import figure_filter, frontier_labs_filter, news_summarizer, translator
from src.processors.editorial_history import similar
from src.processors.source_grounding import grounded_text
from src.renderer.render import _compact_inherited_styles, _factor_repeated_styles
from src.utils import action_evidence, idempotency
from src.utils.brief_audit import archive_delivery, archive_publication
from src.utils.last_good import LastGoodCache
from src.utils.market_clock import calendar, latest_closed_session, validate_quote
from src.valuation import qqqm, qqqm_sources
from tests.test_qqqm_consistency import packet  # noqa: F401 - reusable complete fixture

NOW = datetime(2026, 9, 22, 0, tzinfo=UTC)


def test_five_consecutive_quarters_are_valid_and_conflicting_backup_still_rejected(monkeypatch):
    dates = ["2025-06-23", "2025-09-22", "2025-12-22", "2026-03-23", "2026-06-22", "2026-09-21"]
    payload = {
        "cusip": "46138G649",
        "currencyCode": "USD",
        "distributions": [{"exDate": day, "distributionAmountPerUnit": 0.25} for day in dates],
    }
    rows = qqqm_sources.dividend_rows(payload, anchor=date(2026, 9, 21))
    assert len(rows) == 5
    backup = copy.deepcopy(payload)
    backup["distributions"].pop()
    monkeypatch.setattr(
        qqqm_sources, "_fetch", lambda url: payload if url == qqqm_sources.DIV_URL else {}
    )
    monkeypatch.setattr(qqqm_sources, "fetch_gurufocus_pe", lambda **_: None)
    monkeypatch.setattr(qqqm_sources, "fetch_dividend_backup", lambda **_: backup)
    diagnostics = []
    token = qqqm_sources.SOURCE_DIAGNOSTICS.set(diagnostics.append)
    try:
        assert qqqm_sources.fetch_source_packet(checked_at=NOW) is None
    finally:
        qqqm_sources.SOURCE_DIAGNOSTICS.reset(token)
    assert any("冲突" in row.get("reason", "") for row in diagnostics)


@pytest.mark.parametrize(
    "dates",
    [
        ["2025-09-22", "2025-12-22", "2026-03-23", "2026-03-24", "2026-09-21"],
        ["2025-09-22", "2025-12-22", "2026-03-23", "2026-09-21"],
        ["2025-09-22", "2025-12-22", "2026-03-23", "2026-06-22", "2026-06-22"],
    ],
)
def test_bad_dividend_quarters_rejected(dates):
    payload = {
        "cusip": "46138G649",
        "currencyCode": "USD",
        "distributions": [
            {"exDate": day, "distributionAmountPerUnit": 1} for day in ["2025-06-23", *dates]
        ],
    }
    with pytest.raises(ValueError):
        qqqm_sources.dividend_rows(payload, anchor=date(2026, 9, 21))


def test_nav_error_has_expected_and_actual_date():
    with pytest.raises(ValueError, match="expected_date=2026-09-21 actual_date=2026-09-18"):
        qqqm_sources.build_source_packet(
            {"cusip": "46138G649", "currency": "USD", "effectiveDate": "2026-09-18"},
            {"cusip": "46138G649", "currencyCode": "USD"},
            None,
            checked_at=NOW,
            nav_history={},
        )


def test_round_diagnostics_survive_retry_and_timeout(monkeypatch, tmp_path):
    monkeypatch.setattr(qqqm, "_BOOTSTRAP_PATH", tmp_path / "missing")

    def failed(**kwargs):
        qqqm_sources._diagnose(
            expected_date="2026-09-21", actual_date="2026-09-18", nav_history_status="rejected"
        )
        return None

    monkeypatch.setattr(qqqm, "fetch_source_packet", failed)
    client = Mock()
    for now in [NOW, NOW + timedelta(minutes=2)]:
        assert qqqm.prepare_qqqm_display(
            price=300, client=client, state_dir=tmp_path, checked_at=now
        ).is_pending
    qqqm.cached_qqqm_display(price=300, state_dir=tmp_path, checked_at=NOW + timedelta(minutes=3))
    audit = json.loads((tmp_path / qqqm._AUDIT_NAME).read_text())
    assert "2026-09-18" in json.dumps(audit["prior_rounds"])
    assert len(audit["prior_rounds"]) >= 2
    client.search_web.assert_not_called()


def test_expired_cache_uses_source_date_not_restore_time(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("QQQM_DAILY_FORWARD_ENABLED", "true")
    payload = json.loads(qqqm._BOOTSTRAP_PATH.read_text())
    payload["verified_at"] = NOW.isoformat()
    path = tmp_path / "qqqm_valuation.json"
    path.write_text(json.dumps(payload))
    assert qqqm._from_cache(path, price=300, checked_at=NOW, allow_daily_forward=True) is None
    assert "data_date=2026-09-02" in caplog.text


@pytest.mark.parametrize(
    "symbol,now,day,close_hour",
    [
        ("MSFT", datetime(2026, 9, 22, 0, tzinfo=UTC), "2026-09-21", 20),
        ("MSFT", datetime(2026, 11, 27, 19, tzinfo=UTC), "2026-11-27", 18),
        ("0700.HK", datetime(2026, 9, 22, 0, tzinfo=UTC), "2026-09-21", 8),
    ],
)
def test_intraday_quote_never_passes_for_close(symbol, now, day, close_hour):
    cal = calendar(symbol, now.year)
    opened = cal.session_open(day).to_pydatetime() + timedelta(minutes=1)
    with pytest.raises(ValueError, match="盘中"):
        validate_quote(opened.timestamp(), symbol=symbol, now=now)
    assert (
        validate_quote(cal.session_close(day).timestamp(), symbol=symbol, now=now).isoformat()
        == day
    )


def test_us_holiday_and_hk_half_day():
    assert latest_closed_session("MSFT", datetime(2026, 9, 7, 23, tzinfo=UTC)) == date(2026, 9, 4)
    cal = calendar("0700.HK", 2026)
    close = cal.session_close("2026-12-24").to_pydatetime()
    assert latest_closed_session("0700.HK", close + timedelta(minutes=1)) == date(2026, 12, 24)


@pytest.mark.parametrize("bad", ["date", "currency", "symbol", "close"])
def test_chart_close_identity_is_bound_to_response(monkeypatch, bad):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    monkeypatch.setattr(stocks, "datetime", Clock)
    meta = {
        "symbol": "MSFT",
        "currency": "USD",
        "exchangeName": "NMS",
        "regularMarketPrice": 999999,
    }
    stamp = datetime(2026, 9, 21, 13, 30, tzinfo=UTC).timestamp()
    result = {"meta": meta, "timestamp": [stamp], "indicators": {"quote": [{"close": [420]}]}}
    if bad == "date":
        result["timestamp"] = [stamp - 86400 * 3]
    if bad == "currency":
        meta["currency"] = "HKD"
    if bad == "symbol":
        meta["symbol"] = "AAPL"
    if bad == "close":
        result["indicators"]["quote"][0]["close"] = [None]
    monkeypatch.setattr(
        stocks.requests,
        "get",
        lambda *a, **k: SimpleNamespace(
            status_code=200, json=lambda: {"chart": {"result": [result]}}
        ),
    )
    with pytest.raises(ValueError):
        stocks._yahoo_chart_history("MSFT", interval="1d", period="5d", minimum=1)


def test_invalid_latest_vix_and_2020_history_rejected(monkeypatch):
    for values, dates in [
        ([17, 18], ["2020-09-17", "2020-09-18"]),
        ([17, None], ["2026-09-18", "2026-09-21"]),
    ]:
        with pytest.raises(ValueError):
            sentiment._dated_values(values, dates, source="CBOE", key="VIX")


def test_cache_reads_do_not_renew_observation_age(tmp_path):
    cache = LastGoodCache(tmp_path)
    value = {
        "current": 18,
        "observed_at": "2026-09-18",
        "source": "CBOE",
        "fetched_at": "2026-09-19T00:00:00+00:00",
    }
    cache.put(
        "sentiment.VIX",
        value,
        today=date(2026, 9, 22),
        observed_at=value["observed_at"],
        source="CBOE",
    )
    before = (tmp_path / "last_good.json").read_bytes()
    for day in [date(2026, 9, 22), date(2026, 9, 23)]:
        result = sentiment._with_last_good(
            lambda: sentiment.SentimentMetric("VIX", None, None, None, error="failed"),
            cache=cache,
            cache_key="VIX",
            today=day,
        )
        assert result.stale_from == "2026-09-18"
    assert (tmp_path / "last_good.json").read_bytes() == before
    result = sentiment._with_last_good(
        lambda: sentiment.SentimentMetric("VIX", None, None, None, error="failed"),
        cache=cache,
        cache_key="VIX",
        today=date(2026, 9, 26),
    )
    assert result.current is None


def test_frequency_rules_differ():
    sentiment._validate_observation("2026-09-18", "FREDHY", date(2026, 9, 22), NOW)
    sentiment._validate_observation("2026-09-01", "ShillerPE", date(2026, 9, 22), NOW)
    with pytest.raises(ValueError):
        sentiment._validate_observation("2026-09-18", "VIX", date(2026, 9, 22), NOW)


@pytest.mark.parametrize("kind", ["old", "invalid", "gap"])
def test_crude_window_rejected(kind):
    dates = pd.bdate_range(end="2026-09-21", periods=20)
    values = [70] * 20
    if kind == "old":
        dates = pd.bdate_range(end="2020-09-21", periods=20)
    if kind == "invalid":
        values[-1] = float("nan")
    if kind == "gap":
        dates = list(dates)
        dates[0] = pd.Timestamp("2025-01-01")
    with pytest.raises(ValueError):
        jiangsu_fuel._validated_crude_window(values, dates, today=date(2026, 9, 22), max_age_days=4)


@pytest.mark.parametrize(
    "a,b",
    [
        ("美联储降息25基点", "美联储降息50基点"),
        ("微软已获监管批准", "微软未获监管批准"),
        ("Microsoft has approval", "Microsoft has no approval"),
        ("微软计划收购甲公司", "微软完成收购甲公司"),
        ("微软收入100亿美元", "微软收入100万元"),
        ("微软收购甲公司", "微软收购乙公司"),
        ("9月21日发布", "9月22日发布"),
        ("盈利1.5亿元", "盈利15亿元"),
    ],
)
def test_fact_changes_survive_every_similarity_gate(a, b):
    for fn in [
        company_news._similar,
        macro_news._similar,
        figure_filter._similar,
        frontier_labs_filter._similar,
        similar,
    ]:
        assert not fn(a, b)
        assert fn(a, a)


def test_long_title_and_same_url_update_get_distinct_keys():
    first = company_news.NewsItem(
        "Microsoft " + "long " * 30 + "not approved", NOW, "https://example.com", "Reuters"
    )
    second = copy.copy(first)
    second.title = first.title.replace("not approved", "approved")
    assert company_news._content_hash("MSFT", first) != company_news._content_hash("MSFT", second)
    second.title = first.title
    second.summary = "New amount $100 billion"
    assert company_news._content_hash("MSFT", first) != company_news._content_hash("MSFT", second)


def test_translation_never_mutates_original():
    item = company_news.NewsItem(
        "Microsoft has not received approval",
        NOW,
        "https://example.com",
        "Reuters",
        summary="Original summary",
    )
    client = SimpleNamespace(
        chat=lambda *a, **k: SimpleNamespace(text="▦ 1: Microsoft 已获批准", error=None)
    )
    translator.translate_in_place_news([item], client=client)
    assert item.title == "Microsoft has not received approval"
    assert item.summary == "Original summary"
    assert item.translated_title == "Microsoft 已获批准"


def test_same_company_and_valid_citation_cannot_launder_false_fact():
    item = company_news.NewsItem(
        "微软尚未获监管批准，交易金额仍待确认",
        NOW,
        "https://example.com/source",
        "Reuters",
        holding_ticker="MSFT",
    )
    summary = news_summarizer._rebuild_safe_summary(
        "<strong>微软</strong>——已获批准，金额1000亿美元[1]", [item]
    )
    assert "1000亿" not in summary.summary_html
    assert "尚未获监管批准" in summary.summary_html
    assert summary.evidence[0]["excerpt"] == item.title
    assert summary.evidence[0]["mode"] == "source_extract"


def test_substring_omitting_negation_is_not_verified():
    item = SimpleNamespace(
        title="Microsoft has not approved the acquisition.",
        summary="",
        url="https://example.com",
        published_at=NOW,
    )
    _, mapping = grounded_text("approved the acquisition.", [item])
    assert mapping[0]["mode"] == "source_extract"
    _, mapping = grounded_text(item.title, [item])
    assert mapping[0]["mode"] == "verified_extract"


def _jobs(*steps, attempt=1):
    return {
        "run_attempt": attempt,
        "steps": [
            {"name": name, "conclusion": "success", "completed_at": stamp} for name, stamp in steps
        ],
    }


def test_jobs_all_attempts_pagination_and_receipt_edition():
    calls = []

    def get(url):
        calls.append(url)
        assert "filter=all" in url
        if "page=2" in url:
            return {
                "jobs": [_jobs(("Confirm SMTP acceptance | 2026-09-22", "2026-09-22T17:00:00Z"))]
            }
        return {"jobs": [_jobs(attempt=2) for _ in range(100)]}

    evidence = action_evidence.run_evidence(
        get, "owner/repo", 42, today="2026-09-22", exclude_attempt=2
    )
    assert evidence["accepted"] and not evidence["full"]
    assert len(calls) == 2


@pytest.mark.parametrize("accepted", [True, False])
def test_same_run_rerun_checks_prior_attempt(monkeypatch, accepted):
    for key, val in {
        "GH_TOKEN": "fixture",
        "GH_REPO": "owner/repo",
        "GH_RUN_ID": "42",
        "GH_RUN_ATTEMPT": "2",
    }.items():
        monkeypatch.setenv(key, val)
    monkeypatch.setattr(idempotency, "_today_beijing_iso", lambda: "2026-09-22")

    def get(url, token):
        if "/jobs?" in url:
            return {
                "jobs": [
                    _jobs(
                        *([("Confirm SMTP acceptance", "2026-09-21T23:10:00Z")] if accepted else [])
                    )
                ]
            }
        return {
            "workflow_runs": [
                {
                    "id": 42,
                    "created_at": "2025-01-01T00:00:00Z",
                    "updated_at": "2026-09-22T00:00:00Z",
                    "status": "in_progress",
                }
            ]
        }

    monkeypatch.setattr(idempotency, "_github_json", get)
    assert idempotency.already_sent_today() is accepted


def test_quality_warning_does_not_erase_delivery(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "fixture")
    monkeypatch.setenv("GH_REPO", "owner/repo")
    monkeypatch.setattr(monitor, "should_send_today", lambda _: (True, ""))
    monkeypatch.setattr(monitor, "_today_beijing_iso", lambda: "2026-09-22")

    def get(url, token):
        if "/jobs?" in url:
            return {
                "jobs": [
                    _jobs(
                        *[
                            (name, "2026-09-21T23:10:00Z")
                            for name in [
                                "Confirm full email delivery",
                                "Report content quality warning",
                            ]
                        ]
                    )
                ]
            }
        return {
            "workflow_runs": [
                {
                    "id": 42,
                    "created_at": "2025-01-01T00:00:00Z",
                    "updated_at": "2026-09-22T00:00:00Z",
                }
            ]
        }

    monkeypatch.setattr(monitor, "_github_json", get)
    ok, reason = monitor.check_today_status()
    assert not ok and "内容降级" in reason and "禁止整封重发" in reason


def test_archive_exact_bodies_hashes_attempt_and_delivery(tmp_path, monkeypatch):
    monkeypatch.setenv("BRIEF_AUDIT_DIR", str(tmp_path))
    monkeypatch.setenv("GH_RUN_ID", "42")
    monkeypatch.setenv("GH_RUN_ATTEMPT", "2")
    html = '<html><body><p>原始正文100美元</p><a href="https://example.com">来源</a></body></html>'
    folder = archive_publication(
        html,
        generated_at=NOW,
        report={
            "status": "degraded",
            "counts": {"missing": 1},
            "news_coverage": {},
            "summary_mapping": {},
        },
    )
    folder = archive_delivery(
        folder,
        {
            "status": "partial",
            "accepted_count": 1,
            "refused_count": 1,
            "sent_at": "2026-09-23T00:00:01+08:00",
            "edition": "2026-09-23",
            "recipient": "secret@example.com",
        },
    )
    manifest = json.loads((folder / "manifest.json").read_text())
    assert folder.name == "42-2-2026-09-23"
    assert (folder / "email.html").read_text() == html
    assert manifest["sha256"]["html"] == hashlib.sha256(html.encode()).hexdigest()
    assert (
        manifest["delivery"]["status"] == "partial" and manifest["content"]["status"] == "degraded"
    )
    assert "secret@example.com" not in (folder / "manifest.json").read_text()


def test_compaction_preserves_all_text_and_links():
    html = (
        "<html><head></head><body>"
        + "".join(
            f'<p style="font-family:serif;color:#123456;font-size:16px;line-height:1.9">完整正文{i} <a href="https://example.com/{i}">来源</a></p>'
            for i in range(100)
        )
        + "</body></html>"
    )
    compact = _factor_repeated_styles(_compact_inherited_styles(html))
    before, after = BeautifulSoup(html, "html.parser"), BeautifulSoup(compact, "html.parser")
    assert before.body.get_text() == after.body.get_text()
    assert [a["href"] for a in before.select("a")] == [a["href"] for a in after.select("a")]
    assert len(compact.encode()) < len(html.encode())


def test_qqqm_watchdog_does_not_wait_for_worker_join(monkeypatch):
    from concurrent.futures import Future

    from src.utils.runtime_budget import StageTimeout

    class InterruptedPool:
        def __init__(self, **kwargs):
            self.shutdowns = []

        def submit(self, *args, **kwargs):
            future = Future()
            future.set_exception(StageTimeout("qqqm"))
            return future

        def shutdown(self, **kwargs):
            self.shutdowns.append(kwargs)

    pool = InterruptedPool()
    monkeypatch.setattr(qqqm_sources, "ThreadPoolExecutor", lambda **_: pool)
    with pytest.raises(StageTimeout):
        qqqm_sources.fetch_source_packet(checked_at=NOW)
    assert pool.shutdowns == [{"wait": False, "cancel_futures": True}]


def test_macro_unverified_heading_cannot_smuggle_approval_or_amount():
    from src.processors.macro_filter import _rebuild_safe_html

    item = SimpleNamespace(
        title="监管尚未批准交易", summary="", url="https://example.com/a", source="wire"
    )
    html, _ = _rebuild_safe_html("已获批准。监管尚未批准交易[1]", [item])
    assert "已获批准" not in html
    assert "尚未批准" in html


def test_delivery_rejects_ambiguous_local_timestamp(tmp_path, monkeypatch):
    from src.utils.delivery import write_delivery_receipt

    monkeypatch.setenv("DELIVERY_RECEIPT_PATH", str(tmp_path / "receipt.json"))
    with pytest.raises(ValueError, match="timezone-aware"):
        write_delivery_receipt(sent_at=datetime(2026, 9, 22), accepted_count=1, refused_count=0)


def test_successful_acceptance_with_missing_edition_fails_closed():
    payload = {
        "jobs": [
            {
                "run_attempt": 1,
                "steps": [
                    {
                        "name": "Confirm SMTP acceptance | ",
                        "conclusion": "success",
                        "completed_at": "2026-09-21T23:59:00Z",
                    }
                ],
            }
        ]
    }
    with pytest.raises(ValueError, match="edition"):
        action_evidence.run_evidence(lambda url: payload, "owner/repo", 1, today="2026-09-22")


def test_thesis_cannot_strip_negation_from_verified_original():
    from src.processors.thesis.extractor import _filter_grounded_evidence, _grounding_material

    original = "监管机构尚未批准这笔金额为一千亿美元的交易"
    url = "https://example.com/regulatory"
    obj = SimpleNamespace(
        summary_html=original,
        evidence=[
            {
                "original_title": original,
                "excerpt": original,
                "output_text": original,
                "url": url,
            }
        ],
    )
    material = _grounding_material(
        company_news=obj, macro_news=None, figure_summaries=[], frontier_labs_events=[]
    )
    claim = SimpleNamespace(
        source_section="company_news", text="批准这笔金额为一千亿美元的交易", url=url, theme="test"
    )
    assert _filter_grounded_evidence([claim], material) == []


@pytest.mark.parametrize(
    "left,right", [("降息[25]基点", "降息[50]基点"), ("监管已经批准？", "监管已经批准！")]
)
def test_bracketed_numbers_and_questions_are_not_cosmetic(left, right):
    from src.utils.news_facts import equivalent

    assert not equivalent(left, right)
