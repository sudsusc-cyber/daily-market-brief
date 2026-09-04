from __future__ import annotations

import json
import time
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scripts.pop_mart_cache import normalize
from src.valuation.pop_mart import (
    BACKUP_NAME,
    RECOVERY_NAME,
    STATE_NAME,
    AnalystTarget,
    Candidate,
    PopMartTargetProvider,
    load_target,
    parse_article,
    refresh_target,
    save_target,
    target_display,
    validate_target,
)
from src.valuation.service import cached_morningstar_displays, prepare_valuation_displays

NOW = datetime(2026, 9, 5, tzinfo=UTC)
CONFIG = Path(__file__).parents[1] / "config"
SINA = "https://finance.sina.com.cn/stock/usstock/c/2026-08-21/doc-ininztwt6444665.shtml"
AA = "https://secure.aastocks.com/tc/mobile/News.aspx?NewsID=NOW.1539842&NewsSource=HK6"
TITLE = "泡泡玛特港股 摩根士丹利下调目标价"
BODY = "泡泡玛特港股业绩公布后，摩根士丹利将该股目标价从214港元下调至203港元，维持增持。现价143.9港元。"


def sina(body=BODY, title=TITLE, published="2026-08-21T09:47:19+08:00"):
    return f'<meta property="article:published_time" content="{published}"><h1>{title}</h1><div id="artibody">{body}</div>'


def aa(body="摩根士丹利发表报告，泡泡瑪特(09992.HK)目標價由214元下調至203元，維持增持。"):
    return '<div id="ctl00_cphContent_pNewsContent"><div class="padding2"><span class="quote_table_header_text">《大行》大摩下調泡泡瑪特(09992.HK)目標價至203元</span> 2026-08-21 11:54:12 <span id="lblContent">' + body + '</span></div></div>'


def observation(value=203, day="2026-08-21"):
    return AnalystTarget(value, f"{day}T09:47:19+08:00", NOW.isoformat(), SINA, "a" * 64)


class Provider:
    def __init__(self, rows=(), discovery_ok=True):
        self.rows = rows
        self.discovery_ok = discovery_ok

    def fetch(self, **kwargs):
        return self.rows, self.discovery_ok


def refresh(tmp_path, rows=(), discovery_ok=True):
    return refresh_target(state_dir=tmp_path, config_dir=CONFIG, checked_at=NOW,
                          price=140, provider=Provider(rows, discovery_ok))


@pytest.mark.parametrize(("url", "html"), [(SINA, sina()), (AA, aa())])
def test_reads_destination_target_not_old_target_or_spot(url, html):
    value = parse_article(url, html, checked_at=NOW)
    assert value.target_price == 203
    assert value.currency == "HKD"
    assert value.institution == "Morgan Stanley"
    assert value.value_type == "analyst_target_price"
    assert value.published_at.startswith("2026-08-21")
    assert len(value.evidence_sha256) == 64


@pytest.mark.parametrize("phrase", [
    "目标价从214港元下调至203港元", "目标价由190港元上调至203港元",
    "目标价维持203港元", "目标价为203港元", "目标价维持在203港元",
])
def test_target_update_and_unchanged_target_phrasings(phrase):
    value = parse_article(SINA, sina("摩根士丹利报告，泡泡玛特港股" + phrase + "。"), checked_at=NOW)
    assert value.target_price == 203


@pytest.mark.parametrize("html", [
    sina(BODY.replace("摩根士丹利", "摩根大通")),
    sina(BODY, TITLE.replace("泡泡玛特", "PDD")),
    sina(BODY + "汇丰目标价至136.5港元。"),
    sina(BODY.replace("203港元", "203美元")),
    sina(BODY.replace("203港元", "203人民币")),
    sina(BODY.replace("203港元", "203%")),
    sina(BODY.replace("203港元", "203倍")),
    sina("泡泡玛特港股摩根士丹利评级增持，股价203港元。"),
    sina(BODY, TITLE + "至210港元"),
    sina(BODY + "目标价为210港元。"),
    sina(BODY, "回顾去年泡泡玛特摩根士丹利目标价"),
    sina(BODY, published="2030-01-01T00:00:00+08:00"),
    sina(BODY, published="2026-08-21"),
    '<h1>' + TITLE + '</h1><div>' + BODY + '</div>',
])
def test_rejects_wrong_identity_currency_conflicts_dates_and_missing_body(html):
    with pytest.raises(ValueError):
        parse_article(SINA, html, checked_at=NOW)


def test_sidebar_does_not_supply_or_change_target():
    html = sina() + '<aside>2026-09-05 摩根士丹利 泡泡玛特目标价为999港元</aside>'
    assert parse_article(SINA, html, checked_at=NOW).target_price == 203


def test_previous_year_operating_metrics_do_not_reject_current_report():
    assert parse_article(AA, aa().replace("維持增持", "去年下半年门店630间，維持增持"), checked_at=NOW).target_price == 203


@pytest.mark.parametrize("url", ["https://evil.test/a", "https://finance.sina.com.cn.evil.test/a", "http://finance.sina.com.cn/a", "https://secure.aastocks.com/tc/mobile/quote.aspx"])
def test_source_allowlist(url):
    with pytest.raises(ValueError):
        parse_article(url, sina(), checked_at=NOW)


@pytest.mark.parametrize("fields", [
    {"currency": "USD"}, {"ticker": "PDD"}, {"institution": "Morningstar"},
    {"value_type": "morningstar_fair_value"}, {"target_price": float("nan")},
    {"target_price": float("inf")}, {"target_price": 0}, {"target_price": -1},
    {"target_price": True}, {"evidence_sha256": ""}, {"verified_at": "2030-01-01T00:00:00Z"},
])
def test_cached_observation_validation(fields):
    with pytest.raises(ValueError):
        validate_target(replace(observation(), **fields), checked_at=NOW)


def test_offline_or_corrupt_runtime_cache_still_displays_verified_seed(tmp_path):
    for name in (STATE_NAME, BACKUP_NAME, RECOVERY_NAME):
        (tmp_path / name).write_text("{broken", encoding="utf-8")
    result = refresh(tmp_path, discovery_ok=False)
    assert result.intrinsic_value == 203
    assert result.implied_return == pytest.approx(203 / 140 - 1)
    assert result.status == "not_due"
    assert "最新报告本次未确认" in result.data_note
    assert result.financial_as_of == "2026-08-21"
    assert result.verified_at.startswith("2026-09-04")  # not forged as today's update


def test_newer_report_wins_regardless_of_lower_target_or_iteration_order(tmp_path):
    result = refresh(tmp_path, [observation(190, "2026-09-04"), observation(203)])
    assert result.intrinsic_value == 190
    assert result.financial_as_of == "2026-09-04"
    assert result.status == "current"
    assert result.formula_id == "analyst_target_price_gap"
    assert "morningstar" not in result.source_document_id
    assert refresh(tmp_path).intrinsic_value == 190


def test_newer_report_with_unchanged_target_refreshes_source_date(tmp_path):
    result = refresh(tmp_path, [observation(203, "2026-09-04")])
    assert result.intrinsic_value == 203
    assert result.financial_as_of == "2026-09-04"


def test_newer_same_day_conflict_retains_old_target_not_blank(tmp_path):
    result = refresh(tmp_path, [observation(190, "2026-09-04"), observation(220, "2026-09-04")])
    assert result.intrinsic_value == 203
    assert result.status == "not_due"
    # A later media syndication hour does not prove an intraday broker revision.
    result = refresh(tmp_path, [replace(observation(210), published_at="2026-08-21T20:00:00+08:00")])
    assert result.intrinsic_value == 203


def test_older_live_article_cannot_override_newer_cached_report(tmp_path):
    refresh(tmp_path, [observation(220, "2026-09-04")])
    result = refresh(tmp_path, [observation(203)])
    assert result.intrinsic_value == 220
    assert result.status == "not_due"


def test_isolated_recovery_cannot_replace_newer_daily_cache(tmp_path):
    save_target(observation(220, "2026-09-04"), state_dir=tmp_path)
    (tmp_path / RECOVERY_NAME).write_text(json.dumps({"observations": [asdict(observation(203))]}))
    assert normalize(tmp_path, CONFIG, checked_at=NOW)
    assert load_target(state_dir=tmp_path, config_dir=CONFIG, checked_at=NOW).target_price == 220
    for name in (STATE_NAME, BACKUP_NAME, RECOVERY_NAME):
        assert json.loads((tmp_path / name).read_text())["observations"][0]["target_price"] == 220


def test_valuation_watchdog_offline_path_includes_numeric_pop_mart(tmp_path):
    result = cached_morningstar_displays(state_dir=tmp_path, config_dir=CONFIG, prices={"9992.HK": 140}, checked_at=NOW)
    assert result["9992.HK"].intrinsic_value == 203
    assert result["9992.HK"].implied_return == pytest.approx(0.45)
    assert result["MSFT"].formula_id == "morningstar_fair_value_1y_irr"


@pytest.mark.parametrize("price", [None, 0, -1, float("inf"), float("nan")])
def test_invalid_spot_does_not_erase_target_or_invent_gap(price):
    result = target_display(observation(), price=price)
    assert result.intrinsic_value == 203
    assert result.implied_return is None


def test_provider_failure_and_disk_failure_do_not_erase_target(tmp_path, monkeypatch):
    class Broken:
        def fetch(self, **kwargs):
            raise TimeoutError("offline")

    def fail_write(*args, **kwargs):
        raise OSError("disk unavailable")

    monkeypatch.setattr(Path, "write_text", fail_write)
    result = refresh_target(state_dir=tmp_path, config_dir=CONFIG, checked_at=NOW, price=140, provider=Broken())
    assert result.intrinsic_value == 203


def test_main_service_excludes_pop_from_morningstar_without_llm(tmp_path, monkeypatch):
    from src.collectors.stocks import StockSignal
    from src.config import HOLDINGS

    holding = next(row for row in HOLDINGS if row.ticker == "9992.HK")
    signal = StockSignal(holding=holding, last_close=140, sma_120=100, sma_200=80,
                         delta_120=.4, delta_200=.75, signal="NONE")

    class Morningstar:
        def fetch_all(self, securities, **kwargs):
            assert "9992.HK" not in securities
            assert "MSFT" in securities and "TSM" in securities
            return {}, {}

    monkeypatch.setattr("src.valuation.service.refresh_target", lambda **kwargs: target_display(observation(), price=140))
    displays, _ = prepare_valuation_displays(signals=[signal], state_dir=tmp_path, config_dir=CONFIG,
                                             checked_at=NOW, morningstar_provider=Morningstar(), reviewer=object())
    assert displays["9992.HK"].intrinsic_value == 203
    assert displays["9992.HK"].warnings == ()


def test_discovery_only_reads_matching_broker_articles(monkeypatch):
    provider = PopMartTargetProvider()
    html = '<a href="/tc/stocks/analysis/stock-aafn-con/09992/AAFN/NOW.123/hk-stock-news">大摩下调泡泡瑪特目标价</a><a href="/tc/stocks/analysis/stock-aafn-con/09992/AAFN/NOW.456/hk-stock-news">汇丰下调泡泡瑪特目标价</a>'
    monkeypatch.setattr(provider, "_get", lambda *args, **kwargs: html)
    assert provider._discover("https://www.aastocks.com/example", deadline=time.monotonic() + 1) == [Candidate("https://secure.aastocks.com/tc/mobile/News.aspx?NewsID=NOW.123&NewsSource=HK6")]


def test_provider_requires_two_agreeing_full_article_reads(monkeypatch):
    import threading

    provider = PopMartTargetProvider()
    monkeypatch.setattr("src.valuation.pop_mart._DISCOVERY", (
        "https://www.aastocks.com/", "https://stock.finance.sina.com.cn/",
    ))
    monkeypatch.setattr(provider, "_discover", lambda url, **kwargs: [SINA] if "sina" in url else [AA])
    counts, lock = {}, threading.Lock()

    def get(url, **kwargs):
        with lock:
            counts[url] = counts.get(url, 0) + 1
            count = counts[url]
        return aa() if url == AA else sina(BODY.replace("203港元", "210港元") if count == 2 else BODY)

    monkeypatch.setattr(provider, "_get", get)
    rows, healthy = provider.fetch(known=observation(), checked_at=NOW)
    assert [row.source_url for row in rows] == [AA]
    assert healthy is False


def test_new_discovered_but_unreadable_report_marks_retained_even_if_seed_readable(monkeypatch, tmp_path):
    provider = PopMartTargetProvider()
    newer = SINA.replace("ininztwt6444665", "newerreport123")
    monkeypatch.setattr(provider, "_discover", lambda *args, **kwargs: [newer])

    def get(url, **kwargs):
        if url == newer:
            raise TimeoutError("new report unavailable")
        return aa() if url == AA else sina()

    monkeypatch.setattr(provider, "_get", get)
    result = refresh_target(state_dir=tmp_path, config_dir=CONFIG, checked_at=NOW, price=140, provider=provider)
    assert result.intrinsic_value == 203
    assert result.status == "not_due"


@pytest.mark.parametrize("retained", [False, True])
def test_email_keeps_numeric_cell_and_identifies_non_morningstar_exception(retained):
    from bs4 import BeautifulSoup

    from scripts.preview_email import _build_mock_signals, _build_mock_valuations
    from src.renderer.render import render_email

    signals = _build_mock_signals()
    valuations = _build_mock_valuations()
    valuations["9992.HK"] = target_display(observation(), price=140, retained=retained)
    html = render_email(signals=signals, generated_at=NOW, valuations=valuations)
    soup = BeautifulSoup(html, "html.parser")
    cell = soup.select_one('[data-holding="9992.HK"] .holding-valuation')
    assert cell.select_one('.holding-valuation-main').get_text(strip=True) == "203.00"
    assert cell.select_one('.holding-implied-return').get_text(strip=True) == "IRR\xa045.0%"
    assert "待更新" not in cell.get_text()
    assert "其余个股 Morningstar；泡泡玛特为大摩目标价（2026-08-21），非晨星" in html
    assert ("最新报告本次未确认" in html) is retained


def test_no_expiry_of_verified_estimate_during_long_outage(tmp_path):
    value = load_target(state_dir=tmp_path, config_dir=CONFIG, checked_at=datetime(2027, 1, 1, tzinfo=UTC))
    assert value.target_price == 203
    assert value.published_at.startswith("2026-08-21")


def test_partial_legacy_record_is_not_promoted_to_broker_target(tmp_path):
    row = asdict(observation(999, "2026-09-04"))
    del row["value_type"]
    (tmp_path / STATE_NAME).write_text(json.dumps({"observations": [row]}))
    assert load_target(state_dir=tmp_path, config_dir=CONFIG, checked_at=NOW).target_price == 203
