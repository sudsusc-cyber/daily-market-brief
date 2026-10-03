from dataclasses import asdict
from datetime import UTC, datetime

from bs4 import BeautifulSoup

from src.renderer.render import render_email
from src.renderer.valuation_notes import valuation_notes
from src.valuation.models import ValuationDisplay


def display(note, **kwargs):
    return ValuationDisplay(ticker='EXAMPLE', status='not_due', intrinsic_value=203,
                            financial_as_of='2026-08-21', data_note=note, **kwargs)


def test_same_report_carry_and_disagreement_merge_without_mutating_source_evidence():
    note = ('示例企业沿用 2026-08-21 报告目标价（最新报告本次未确认）；'
            '示例企业来源分歧，采用 2026-08-21T15:05:00+08:00 报告值')
    value = display(note, warnings=('source conflict',), verified_at='2026-10-04T00:00:00+08:00')
    before = asdict(value)
    result = valuation_notes({'EXAMPLE': value})
    assert result['items'] == ['示例企业沿用 2026-08-21 报告目标价（最新报告本次未确认；来源分歧）']
    assert result['report_dates'] == {'EXAMPLE': '2026-08-21'}
    assert asdict(value) == before
    assert '2026-10-04' not in result['items'][0]


def test_other_report_dates_and_unknown_information_survive_note_compaction():
    value = display('示例企业沿用 2026-08-21 报告值（新报告未核实）；'
                    '示例企业来源分歧，采用 2026-09-04T09:00:00+08:00 报告值；'
                    '汇率取自独立来源；另一企业沿用 2026-08-21 报告值')
    result = valuation_notes({'EXAMPLE': value})['items']
    assert result == [
        '示例企业沿用 2026-08-21 报告值（新报告未核实）',
        '示例企业采用 2026-09-04 报告值（来源分歧）',
        '汇率取自独立来源',
        '另一企业沿用 2026-08-21 报告值',
    ]


def test_invalid_dates_and_non_report_notes_are_not_silently_rewritten():
    notes = ['示例企业沿用 2026-99-01 报告值', 'QQQM 分红沿用 2026-09-21 数据（NAV 已更新）']
    result = valuation_notes({'EXAMPLE': display('；'.join(notes))})
    assert result == {'items': notes, 'report_dates': {}}


def test_report_timestamp_uses_its_beijing_date_not_utc_or_verification_date():
    value = display('示例企业来源分歧，采用 2026-08-20T17:05:00Z 报告值')
    result = valuation_notes({'EXAMPLE': value})
    assert result['items'] == ['示例企业采用 2026-08-21 报告值（来源分歧）']
    assert result['report_dates'] == {'EXAMPLE': '2026-08-21'}


def test_latin_ticker_keeps_spacing_before_chinese_note():
    result = valuation_notes({'EXAMPLE': display('EXAMPLE 沿用 2026-08-21 报告值')})
    assert result['items'] == ['EXAMPLE 沿用 2026-08-21 报告值']


def test_email_shows_report_date_once_and_keeps_value_status_and_escaping():
    from scripts.preview_email import _build_mock_signals

    value = display('泡泡玛特沿用 2026-08-21 报告目标价（最新报告本次未确认）；'
                    '泡泡玛特来源分歧，采用 2026-08-21T15:05:00+08:00 报告值；'
                    '<script>bad()</script>', value_label='公允价值', formula_id='analyst_target_price_gap')
    html = render_email(signals=_build_mock_signals(), generated_at=datetime(2026, 10, 4, tzinfo=UTC),
                        valuations={'9992.HK': value})
    text = BeautifulSoup(html, 'html.parser').get_text()
    assert text.count('2026-08-21') == 1
    assert '泡泡玛特为大摩目标价，非晨星' in text
    assert '最新报告本次未确认；来源分歧' in text
    assert '203.00' in text
    assert 'T15:05:00+08:00' not in text
    assert '<script>' not in html
