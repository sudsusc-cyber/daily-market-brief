from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import Mock

import pytest

from src.valuation.morningstar import (
    SECURITIES,
    MorningstarFairValue,
    MorningstarPublicProvider,
    _parse_quote_candidates,
    _quote_fair_value_date,
    _reconcile_independent_sources,
)
from src.valuation.yahoo_morningstar import _CURATED_REPORTS, YahooMorningstarProvider


def test_official_quote_discovery_does_not_require_fair_value_in_title():
    text = """## Company Report
[View Archive](http://www.morningstar.com/company-reports?listing=0P000003P7)
### [A wide moat business](http://www.morningstar.com/stocks/xnys/mco/analysis)
[Analyst](http://www.morningstar.com/people/analyst)Sep 4, 2026
## Price vs Fair Value
Price
$441.36
Oct 2, 2026
Fair Value
LOCK|abc
Sep 4, 2026
### Articles & Videos
* #### [Valuation review ![Image](https://example.org/img) Analyst Sep 14, 2026](http://www.morningstar.com/stocks/valuation-review)
## Other
* #### [Footer Oct 3, 2026](http://www.morningstar.com/stocks/unrelated)
"""
    candidates = _parse_quote_candidates(text)
    assert [c.url for c in candidates] == [
        "https://www.morningstar.com/stocks/xnys/mco/analysis",
        "https://www.morningstar.com/stocks/valuation-review",
    ]
    assert _quote_fair_value_date(text) == datetime(2026, 9, 4, tzinfo=UTC)


def test_quote_date_never_uses_request_date_or_price_date():
    assert _quote_fair_value_date("## Price vs Fair Value\nPrice\n$441.36\nOct 2, 2026\nFair Value\nLOCK|abc") is None


@pytest.mark.parametrize("ticker", list(_CURATED_REPORTS))
def test_yahoo_index_discovers_new_report_for_exact_listing(ticker):
    report_id = _CURATED_REPORTS[ticker][0].rsplit("_", 1)[0] + "_1791000000000"
    session = Mock()
    session.get.return_value.text = report_id + " MS_OTHER_AnalystReport_1791000001000 ARGUS_123_AnalystReport_1791000000000"
    provider = YahooMorningstarProvider(session=session)
    rows = provider._index_reports(SECURITIES[ticker])
    assert [r["id"] for r in rows] == [report_id]


def test_empty_search_does_not_disable_other_issuers(monkeypatch):
    monkeypatch.setattr("src.valuation.yahoo_morningstar.time.sleep", lambda _: None)
    session = Mock()
    session.get.return_value.status_code = 200
    session.get.return_value.json.return_value = {"researchReports": []}
    provider = YahooMorningstarProvider(session=session)
    with pytest.raises(ValueError):
        provider._search({"q": "MSFT"})
    assert provider._search_disabled_reason is None
    session.get.return_value.json.return_value = {"researchReports": [{"id": "new"}]}
    assert provider._search({"q": "MCO"})["researchReports"]


@pytest.mark.parametrize("ticker", ["MCO", "COST", "AAPL"])
@pytest.mark.parametrize("amount", [540, 450])
def test_newer_verified_raise_or_cut_wins_over_old_report(ticker, amount):
    s = SECURITIES[ticker]
    old = MorningstarFairValue(ticker, s.provider_code, 520, s.currency,
        "published-research", "2026-07-27", "2026-10-04T00:00:00Z",
        "Morningstar", "https://www.morningstar.com/stocks/old")
    new = replace(old, fair_value=amount, fair_value_updated_at="2026-09-04",
        report_published_at="2026-09-04T20:30:48Z",
        source_url="https://finance.yahoo.com/research/reports/new")
    assert _reconcile_independent_sources(old, new).fair_value == amount


def test_known_new_official_date_marks_old_distributed_value_as_history(monkeypatch):
    s = SECURITIES["MCO"]
    old = MorningstarFairValue("MCO", s.provider_code, 520, "USD", "published-research",
        "2026-07-27", "2026-10-04T00:00:00Z", "Morningstar Yahoo",
        "https://finance.yahoo.com/research/reports/old")
    secondary = Mock()
    secondary.discovery_diagnostics = {}
    secondary.fetch_all.return_value = ({"MCO": old}, {})
    provider = MorningstarPublicProvider(session=Mock(), secondary_provider=secondary)
    def discover(_):
        provider.discovery_diagnostics["MCO"] = {"official_fair_value_date": "2026-09-04"}
        return []
    monkeypatch.setattr(provider, "_discover", discover)
    values, failures = provider.fetch_all({"MCO": s}, checked_at=datetime(2026, 10, 4, tzinfo=UTC))
    assert values["MCO"].fair_value == 520
    assert values["MCO"].stale_cache
    assert "2026-09-04" in failures["MCO"]
    assert values["MCO"].fair_value_updated_at == "2026-07-27"


def test_snapshot_for_another_listing_is_rejected():
    session = Mock()
    session.get.return_value.text = '\"snapshotUrl\":\"https://s.yimg.com/uc/fin/img/ms-reports-thumbnails/0P000001IK_20260928123456.jpg\"'
    provider = YahooMorningstarProvider(session=session)
    with pytest.raises(ValueError, match="上市标识"):
        provider._snapshot_url("https://finance.yahoo.com/research/reports/MS_0P000003P7_AnalystReport_1788555048000/")


def test_undated_directory_entries_require_report_metadata_not_invented_dates():
    from src.valuation.morningstar import _parse_company_report_candidates
    text = '### [New research](http://www.morningstar.com/company-reports/123?listing=0P000003P7)\nSummary without date.'
    assert _parse_company_report_candidates(text) == []
    rows = _parse_company_report_candidates(text, allow_undated=True)
    assert len(rows) == 1 and rows[0].published_at is None


def test_roundup_publication_cannot_make_older_valuation_newer():
    from src.valuation.morningstar import _valuation_data_date
    assert _valuation_data_date('All data is as of Sept. 4.', '2026-09-21') == '2026-09-04'
    s = SECURITIES['AAPL']
    newer = MorningstarFairValue('AAPL', s.provider_code, 300, 'USD', 'published-research',
        '2026-09-15', '2026-10-04T00:00:00Z', 'Morningstar',
        'https://www.morningstar.com/stocks/new')
    roundup = replace(newer, fair_value=290, fair_value_updated_at='2026-09-21',
        valuation_as_of='2026-09-04', source_url='https://www.morningstar.com/stocks/roundup')
    assert _reconcile_independent_sources(roundup, newer).fair_value == 300


def test_future_valuation_data_date_is_rejected():
    from src.valuation.morningstar import _valuation_data_date
    with pytest.raises(ValueError, match='晚于报告'):
        _valuation_data_date('All data is as of October 5, 2026', '2026-10-04')


@pytest.mark.parametrize('escaped', [False, True])
def test_snapshot_parser_skips_stale_and_other_issuer_images(escaped):
    session = Mock()
    current = 'https://s.yimg.com/uc/fin/img/ms-reports-thumbnails/0P000003P7_20260904153002.jpg'
    urls = ['https://s.yimg.com/uc/fin/img/ms-reports-thumbnails/0P000003P7_20260727093002.jpg', current]
    if escaped:
        urls = [url.replace('/', r'\/') for url in urls]
    session.get.return_value.text = ' '.join('"snapshotUrl":"' + url + '"' for url in urls)
    provider = YahooMorningstarProvider(session=session)
    assert provider._snapshot_url('https://finance.yahoo.com/research/reports/MS_0P000003P7_AnalystReport_1788555048000/') == current


def test_yahoo_embedded_index_metadata_binds_image_to_same_report():
    import json
    report_id = 'MS_0P000003P7_AnalystReport_1788555048000'
    url = 'https://s.yimg.com/uc/fin/img/ms-reports-thumbnails/0P000003P7_20260904153002.jpg'
    session = Mock()
    session.get.return_value.text = '<script type="application/json">' + json.dumps({'body': json.dumps({'reports': [
        {'id': report_id, 'snapshotUrl': url},
        {'id': 'MS_OTHER_AnalystReport_1788555048000', 'snapshotUrl': url},
    ]})}) + '</script>'
    provider = YahooMorningstarProvider(session=session)
    rows = provider._index_reports(SECURITIES['MCO'])
    assert len(rows) == 1 and rows[0]['snapshotUrl'] == url


def test_curated_report_images_all_match_their_actual_report_dates():
    for report_id, _, image in _CURATED_REPORTS.values():
        assert YahooMorningstarProvider._snapshot_matches('https://s.yimg.com/uc/fin/img/ms-reports-thumbnails/' + image, report_id)


def test_valueless_new_article_does_not_block_latest_readable_estimate(monkeypatch):
    from src.valuation.morningstar import _Candidate
    security = SECURITIES['AAPL']
    provider = MorningstarPublicProvider(session=Mock(), secondary_provider=None)
    candidates = [
        _Candidate('https://www.morningstar.com/stocks/new-company-news', datetime(2026, 10, 1, tzinfo=UTC)),
        _Candidate('https://www.morningstar.com/stocks/latest-valuation', datetime(2026, 9, 30, tzinfo=UTC)),
    ]
    monkeypatch.setattr(provider, '_discover', lambda _: candidates)
    def read(candidate, _):
        if candidate == candidates[0]:
            raise ValueError('页面未找到可归属于该标的的 Morningstar 公允价值')
        return MorningstarFairValue('AAPL', security.provider_code, 310, 'USD', 'published-research',
            '2026-09-30', '2026-10-04T00:00:00Z', 'Morningstar', candidate.url)
    monkeypatch.setattr(provider, '_read', read)
    values, failures = provider.fetch_all({'AAPL': security}, checked_at=datetime(2026, 10, 4, tzinfo=UTC))
    assert values['AAPL'].fair_value == 310 and not failures


def test_latest_analysis_date_does_not_suppress_earlier_public_value_discovery(monkeypatch):
    import sys
    security = SECURITIES['AAPL']
    session = Mock()
    session.headers = {}
    session.get.return_value.content = b'''<rss><channel><item><source>Morningstar</source><pubDate>Wed, 30 Sep 2026 12:00:00 GMT</pubDate><link>https://news.google.com/rss/articles/test</link></item></channel></rss>'''
    provider = MorningstarPublicProvider(session=session, secondary_provider=None)
    quote = Mock()
    quote.text = '''## Company Report
### [Apple analyst update](http://www.morningstar.com/stocks/xnas/aapl/analysis)
Analyst Oct 1, 2026
## Price vs Fair Value
Fair Value
LOCK|abc
Sep 9, 2026
'''
    monkeypatch.setattr(provider, '_reader_get', lambda _: quote)
    decoder = Mock()
    decoder.new_decoderv1.return_value = {'status': True, 'decoded_url': 'https://www.morningstar.com/stocks/new-apple-value'}
    monkeypatch.setitem(sys.modules, 'googlenewsdecoder', decoder)
    candidates = provider._discover(security)
    assert any(c.url.endswith('/new-apple-value') for c in candidates)
    assert not any(c.url.endswith('/analysis') for c in candidates)
    assert provider.discovery_diagnostics['AAPL']['latest_analysis'][0]['published_at'].startswith('2026-10-01')


def test_public_html_metric_label_and_amount_can_occupy_adjacent_lines():
    from src.valuation.morningstar import _extract_value, _research_text
    html = '<title>Moody earnings | Morningstar</title><h2>Key Morningstar Metrics for Moody</h2><p>Fair Value Estimate</p><p>: $540.00</p>'
    assert _extract_value(_research_text(html), SECURITIES['MCO']) == (540, 'USD')


def test_adjacent_line_support_never_borrows_another_metrics_number():
    from src.valuation.morningstar import _extract_value
    text = 'Title: Moody earnings\nMorningstar\nFair Value Estimate\nRevenue\n$540.00'
    with pytest.raises(ValueError):
        _extract_value(text, SECURITIES['MCO'])


@pytest.mark.parametrize('new_value', [310, 270])
def test_full_refresh_selects_new_dataset_and_corrects_legacy_roundup_cache(tmp_path, monkeypatch, new_value):
    from src.valuation.morningstar import _Candidate, _save_cache, load_cache, refresh_fair_values
    security = SECURITIES['AAPL']
    old = MorningstarFairValue('AAPL', security.provider_code, 290, 'USD', 'published-research',
        '2026-09-21', '2026-10-02T00:00:00Z', 'Morningstar',
        'https://www.morningstar.com/stocks/roundup')
    _save_cache(tmp_path / 'morningstar_fair_values.json', {'AAPL': old})
    provider = MorningstarPublicProvider(session=Mock(), secondary_provider=None)
    candidates = [
        _Candidate(old.source_url, datetime(2026, 9, 21, tzinfo=UTC)),
        _Candidate('https://www.morningstar.com/stocks/new-apple-report', datetime(2026, 9, 15, tzinfo=UTC)),
        _Candidate('https://www.morningstar.com/stocks/failed-report', datetime(2026, 9, 10, tzinfo=UTC)),
    ]
    monkeypatch.setattr(provider, '_discover', lambda s: candidates if s.ticker == 'AAPL' else [])
    def read(candidate, _):
        if candidate == candidates[0]:
            return replace(old, valuation_as_of='2026-09-04')
        if candidate == candidates[1]:
            return replace(old, fair_value=new_value, fair_value_updated_at='2026-09-15',
                valuation_as_of='2026-09-15', source_url=candidate.url)
        raise ValueError('来源网络不可用')
    monkeypatch.setattr(provider, '_read', read)
    values, _ = refresh_fair_values(provider=provider, state_dir=tmp_path, prices={},
        checked_at=datetime(2026, 10, 4, tzinfo=UTC), baseline_path=tmp_path/'none.json')
    assert values['AAPL'].fair_value == new_value
    assert load_cache(tmp_path/'morningstar_fair_values.json')['AAPL'].fair_value == new_value
    assert values['AAPL'].valuation_as_of == '2026-09-15'


@pytest.mark.parametrize('report_date,needs_news', [('2026-07-27', True), ('2026-09-15', False)])
def test_valid_distributed_date_avoids_redundant_decoding_but_older_report_still_discovers_news(monkeypatch, report_date, needs_news):
    session = Mock()
    session.headers = {}
    session.get.return_value.content = b'<rss><channel/></rss>'
    provider = MorningstarPublicProvider(session=session, secondary_provider=None)
    security = SECURITIES['AAPL']
    provider._validated_secondary['AAPL'] = MorningstarFairValue('AAPL', security.provider_code, 290,
        'USD', 'published-research', report_date, '2026-10-04T00:00:00Z', 'Morningstar Yahoo',
        'https://finance.yahoo.com/research/reports/verified')
    quote = Mock()
    quote.text = '## Price vs Fair Value\nFair Value\nLOCK|abc\nSep 9, 2026\n'
    monkeypatch.setattr(provider, '_reader_get', lambda _: quote)
    provider._discover(security)
    assert bool(session.get.call_count) == needs_news


def test_timeout_preserves_completed_source_failure_diagnostic():
    provider = MorningstarPublicProvider(session=Mock(), secondary_provider=None)
    def interrupted(_label, _operation, **kwargs):
        provider._inflight_failures['MCO'] = 'new report returned HTTP 429'
        return kwargs['fallback']()
    provider._budget = Mock()
    provider._budget.run.side_effect = interrupted
    _, failures = provider.fetch_all({'MCO': SECURITIES['MCO']}, checked_at=datetime(2026, 10, 4, tzinfo=UTC))
    assert failures['MCO'] == 'new report returned HTTP 429'
