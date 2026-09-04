from __future__ import annotations

import json
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.valuation.pop_mart import (
    Candidate,
    NoMatchingTargetError,
    PopMartTargetProvider,
    _channel,
    parse_article,
    refresh_target,
)

NOW = datetime(2026, 9, 5, tzinfo=UTC)
CONFIG = Path(__file__).parents[1] / "config"
URLS = {
    "aastocks": "https://secure.aastocks.com/tc/mobile/News.aspx?NewsID=NOW.9999999&NewsSource=HK6",
    "sina": "https://finance.sina.com.cn/stock/2026-09-04/doc-newreport123.shtml",
    "moneydj": "https://m.moneydj.com/f1a.aspx?a=00000000-1111-2222-3333-444444444444",
    "etnet": "https://www.etnet.com.hk/www/tc/stocks/realtime/quote_news_detail.php?newsid=20260904992&section=research&code=9992",
}


def article(channel, value=250, day="2026-09-04"):
    title = f"大摩上調泡泡瑪特(09992.HK)目標價至{value}港元"
    body = f"摩根士丹利上調泡泡瑪特(09992.HK)目標價由203港元至{value}港元。"
    published = day + "T08:00:00+08:00"
    if channel == "sina":
        return f'<meta property="article:published_time" content="{published}"><h1>{title}</h1><div id="artibody">{body}</div>'
    if channel == "aastocks":
        return f'<div id="x_pNewsContent"><div class="padding2"><span class="quote_table_header_text">{title}</span> {day} 08:00:00 <span id="lblContent">{body}</span></div></div>'
    if channel == "moneydj":
        title = "泡泡瑪特獲外資調整目標價"
        data = json.dumps({"@type": "NewsArticle", "headline": title, "datePublished": published})
        return f'<script type="application/ld+json">{data}</script><h1 id="NewsHD">{title}</h1><div id="f1a_newsData"><p>摩根大通給泡泡瑪特目標價120港元。</p><p>{body}</p><p>匯豐給泡泡瑪特目標價136.5港元。</p></div>'
    date = datetime.fromisoformat(day).strftime("%d/%m/%Y")
    return f'<h1 class="ArticleHdr">大行最新評級</h1><div class="DivArticleList" itemtype="https://schema.org/Article"><span class="date">{date} 08:00</span></div><div id="NewsContent">{table(value)}</div>'


def table(value=250, broker="大摩", issuer="泡泡瑪特 (09992)"):
    return '<table><tr><td>股份/編號</td><td>大行/券商</td><td>目標價變動（元）</td><td>評級變動</td><td>前收市價</td></tr>' + f'<tr><td>{issuer}</td><td>{broker}</td><td>203.00→{value:.2f}</td><td>維持增持</td><td>143.90</td></tr></table>'


@pytest.mark.parametrize("channel", URLS)
def test_each_channel_can_supply_a_new_absolute_target(channel):
    row = parse_article(URLS[channel], article(channel), checked_at=NOW)
    assert row.target_price == 250
    assert row.published_at.startswith("2026-09-04")
    assert row.institution == "Morgan Stanley"


def test_moneydj_separates_brokers_instead_of_taking_first_or_last_number():
    value = parse_article(URLS["moneydj"], article("moneydj"), checked_at=NOW)
    assert value.target_price == 250  # neither JPM 120 nor HSBC 136.5


def test_moneydj_cannot_take_a_different_issuers_morgan_stanley_paragraph():
    html = article("moneydj").replace("摩根士丹利上調泡泡瑪特(09992.HK)", "摩根士丹利上調騰訊(00700.HK)")
    with pytest.raises(NoMatchingTargetError):
        parse_article(URLS["moneydj"], html, checked_at=NOW)


def test_etnet_ubs_203_1_is_not_morgan_stanley_203():
    html = article("etnet").replace(table(), table(203.1, broker="瑞銀"))
    with pytest.raises(NoMatchingTargetError):
        parse_article(URLS["etnet"], html, checked_at=NOW)


def test_etnet_historical_second_table_does_not_get_current_date():
    html = article("etnet").replace(table(), table(203.1, broker="瑞銀") + '<p>較早前</p>' + table(214))
    with pytest.raises(NoMatchingTargetError):
        parse_article(URLS["etnet"], html, checked_at=NOW)


def test_etnet_blank_issuer_cells_carry_forward_only_within_current_issuer():
    current = table(broker="瑞銀").replace('</table>', '<tr><td></td><td>大摩</td><td>203.00→260.00</td><td>增持</td><td></td></tr></table>')
    assert parse_article(URLS["etnet"], article("etnet").replace(table(), current), checked_at=NOW).target_price == 260
    unrelated = current.replace("泡泡瑪特 (09992)", "吉利 (00175)")
    with pytest.raises(NoMatchingTargetError):
        parse_article(URLS["etnet"], article("etnet").replace(table(), unrelated), checked_at=NOW)


def test_etnet_narrative_report_supported_as_well_as_table():
    html = article("etnet").replace("大行最新評級", "大摩上調泡泡瑪特(09992.HK)目標價至250港元")
    html = html.replace(table(), "摩根士丹利將泡泡瑪特目標價由203港元上調至250港元。")
    assert parse_article(URLS["etnet"], html, checked_at=NOW).target_price == 250


def test_moneydj_keyword_discovery_is_dated_and_sorted_not_hardcoded(monkeypatch):
    provider = PopMartTargetProvider()
    raw = [
        {"Title": "泡泡瑪特外資目標價", "Date": "2026-08-21T15:05:00", "Url": "f1a.aspx?a=11111111-1111-2222-3333-444444444444"},
        {"Title": "泡泡瑪特外資目標價", "Date": "2026-09-04T08:00:00", "Url": URLS["moneydj"].split('/')[-1]},
        {"Title": "泡泡瑪特外資目標價", "Date": "2026-09-04T08:00:00", "Url": "https://evil.test/article"},
    ]
    monkeypatch.setattr(provider, "_get", lambda *args, **kwargs: json.dumps(raw))
    found = provider._discover("https://m.moneydj.com/indexPart/search_news.aspx?k=test", deadline=time.monotonic()+1)
    assert len(found) == 2
    assert found[0].url == URLS["moneydj"]
    assert found[0].published_at == "2026-09-04T08:00:00+08:00"


def test_etnet_discovery_uses_visible_date_not_news_id(monkeypatch):
    provider = PopMartTargetProvider()
    url = 'quote_news_detail.php?newsid=20260510250&section=research&code=9992'
    row = f'<div class="DivArticleList dotLine"><span class="date">14/05/2026 10:20</span><a href="{url}">大行報告</a></div>'
    monkeypatch.setattr(provider, "_get", lambda *args, **kwargs: row+row)
    found = provider._discover("https://www.etnet.com.hk/www/tc/stocks/realtime/quote_news_list.php", deadline=time.monotonic()+1)
    assert len(found) == 1
    assert found[0].published_at == "2026-05-14T10:20:00+08:00"


def configured_provider(monkeypatch, blocked=(), values=None, budget=24):
    provider = PopMartTargetProvider(budget_seconds=budget)
    values = values or {channel: 250 for channel in URLS}

    def discover(url, **kwargs):
        channel = _channel(url)
        if channel in blocked:
            raise TimeoutError("channel offline")
        return [Candidate(URLS[channel], "2026-09-04T08:00:00+08:00")]

    def get(url, **kwargs):
        channel = _channel(url)
        if channel in blocked:
            raise TimeoutError("channel offline")
        return article(channel, values[channel])

    monkeypatch.setattr(provider, "_discover", discover)
    monkeypatch.setattr(provider, "_get", get)
    return provider


@pytest.mark.parametrize("blocked", [(channel,) for channel in URLS] + [("aastocks", "sina"), ("aastocks", "sina", "moneydj")])
def test_new_value_still_updates_when_any_primary_or_three_channels_fail(monkeypatch, tmp_path, blocked):
    provider = configured_provider(monkeypatch, blocked)
    result = refresh_target(state_dir=tmp_path, config_dir=CONFIG, checked_at=NOW, price=140, provider=provider)
    assert result.intrinsic_value == 250  # a new report, not merely seed 203
    assert result.status == "current"
    assert result.financial_as_of == "2026-09-04"
    audit = json.loads((tmp_path / "pop_mart_source_diagnostic.json").read_text())
    assert audit["selected"]["target_price"] == 250
    assert audit["retained"] is False


def test_all_four_channels_offline_retains_dated_seed(monkeypatch, tmp_path):
    provider = configured_provider(monkeypatch, tuple(URLS))
    result = refresh_target(state_dir=tmp_path, config_dir=CONFIG, checked_at=NOW, price=140, provider=provider)
    assert result.intrinsic_value == 203 and result.status == "not_due"
    assert result.financial_as_of == "2026-08-21"


def test_one_slow_discovery_cannot_use_all_time_before_peers_read(monkeypatch, tmp_path):
    provider = configured_provider(monkeypatch, budget=1)
    original = provider._discover
    release, finished = threading.Event(), threading.Event()

    def discover(url, **kwargs):
        if _channel(url) == "aastocks":
            release.wait(5)
            finished.set()
            raise TimeoutError("blocked discovery")
        return original(url, **kwargs)

    monkeypatch.setattr(provider, "_discover", discover)
    try:
        result = refresh_target(state_dir=tmp_path, config_dir=CONFIG, checked_at=NOW, price=140, provider=provider)
        # Prove peers finished while discovery remains blocked, without a
        # sub-200ms wall-clock assertion that measures CI scheduling/disk speed.
        assert not finished.is_set()
        assert result.intrinsic_value == 250
        assert provider.diagnostic["timed_out_channels"] == 1
    finally:
        release.set()
        assert finished.wait(5)


def test_inflight_newer_report_is_not_silently_declared_fully_checked(monkeypatch, tmp_path):
    provider = configured_provider(monkeypatch, budget=1)
    original_discover, original_get = provider._discover, provider._get
    release, finished = threading.Event(), threading.Event()

    def discover(url, **kwargs):
        if _channel(url) == "aastocks":
            return [Candidate(URLS["aastocks"], "2026-09-05T08:00:00+08:00")]
        return original_discover(url, **kwargs)

    def get(url, **kwargs):
        if url == URLS["aastocks"]:
            release.wait(5)
            finished.set()
            raise TimeoutError("blocked newer article")
        return original_get(url, **kwargs)

    monkeypatch.setattr(provider, "_discover", discover)
    monkeypatch.setattr(provider, "_get", get)
    try:
        result = refresh_target(state_dir=tmp_path, config_dir=CONFIG, checked_at=NOW, price=140, provider=provider)
        assert not finished.is_set()
        assert result.intrinsic_value == 250
        assert result.status == "not_due"
        assert provider.diagnostic["unresolved_reports"]
    finally:
        release.set()
        assert finished.wait(5)


def test_two_same_day_broker_values_still_do_not_average(monkeypatch, tmp_path):
    values = {channel: 250 for channel in URLS}
    values["moneydj"] = 280
    provider = configured_provider(monkeypatch, values=values)
    result = refresh_target(state_dir=tmp_path, config_dir=CONFIG, checked_at=NOW, price=140, provider=provider)
    assert result.intrinsic_value == 203
    assert result.status == "not_due"
