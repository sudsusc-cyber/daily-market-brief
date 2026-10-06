"""Real watchdog and whole-day rendering regressions; no external sources/SMTP."""
import time
from datetime import date, datetime
from unittest.mock import Mock

import pytest

from src.collectors import jiangsu_fuel
from src.main import _collect_fuel_alert
from src.renderer.render import render_email
from src.utils.dates import BEIJING, to_beijing
from src.utils.runtime_budget import RuntimeBudget, reset_timeout_events, timeout_events


@pytest.fixture(autouse=True)
def isolated_timeouts(monkeypatch):
    monkeypatch.delenv('BRIEF_LLM_CUTOFF_EPOCH', raising=False)
    reset_timeout_events()
    yield
    reset_timeout_events()


@pytest.mark.parametrize('day', [date(2026, 10, 13), date(2026, 10, 14), date(2026, 10, 15), date(2026, 1, 31)])
@pytest.mark.parametrize('exhausted', [False, True])
def test_deadline_keeps_known_window_without_publishing_partial_prediction(monkeypatch, day, exhausted):
    forecast = Mock(side_effect=lambda _: time.sleep(0.3))
    monkeypatch.setattr(jiangsu_fuel, '_fetch_forecast_entries', forecast)
    proxy = Mock(side_effect=AssertionError('must not fetch a proxy after deadline'))
    monkeypatch.setattr(jiangsu_fuel, '_estimate_direction_from_crude', proxy)
    degraded = Mock(side_effect=lambda alert: alert)
    alert = _collect_fuel_alert(budget=RuntimeBudget(0 if exhausted else 0.03), today=day,
                               fred_api_key='unused', on_timeout=degraded)
    target = jiangsu_fuel._next_known_window(day)
    assert alert and alert.adjustment_date == target and alert.direction == '待定'
    assert alert.forecast_method == 'schedule_only'
    assert alert.observed_at is alert.fetched_at is alert.forecast_source is alert.forecast_url is None
    assert forecast.call_count == (0 if exhausted else 1)
    proxy.assert_not_called()
    degraded.assert_called_once_with(alert)
    assert timeout_events()[-1]['reason'] == ('budget_exhausted' if exhausted else 'timeout')
    html = render_email(signals=[], generated_at=datetime.combine(day, datetime.min.time(), BEIJING),
                        jiangsu_fuel_alert=alert)
    assert '油价预告' in html and f'{target.month} 月 {target.day} 日 24 时' in html
    assert '涨跌方向与幅度待更新' in html
    assert '媒体预测' not in html and '模型代理' not in html


@pytest.mark.parametrize('day', [date(2026, 10, 6), date(2026, 10, 12), date(2026, 10, 16), date(2026, 1, 30)])
def test_deadline_does_not_expand_reminder_days(monkeypatch, day):
    forecast = Mock(side_effect=AssertionError('no forecast I/O outside window'))
    monkeypatch.setattr(jiangsu_fuel, '_fetch_forecast_entries', forecast)
    alert = _collect_fuel_alert(budget=RuntimeBudget(0), today=day, fred_api_key='', on_timeout=lambda alert: alert)
    assert alert is None
    forecast.assert_not_called()
    assert '油价预告' not in render_email(signals=[], generated_at=datetime.combine(day, datetime.min.time(), BEIJING),
                                        jiangsu_fuel_alert=alert)


def test_resolved_future_year_schedule_survives_forecast_timeout(monkeypatch):
    monkeypatch.setattr(jiangsu_fuel, '_discover_next_window', lambda _: date(2027, 1, 8))
    monkeypatch.setattr(jiangsu_fuel, '_fetch_forecast_entries', lambda _: time.sleep(0.3))
    alert = _collect_fuel_alert(budget=RuntimeBudget(0.03), today=date(2027, 1, 6), fred_api_key='', on_timeout=lambda alert: alert)
    assert alert and alert.adjustment_date == date(2027, 1, 8) and alert.days_until == 2
    assert alert.forecast_method == 'schedule_only' and alert.observed_at is None


def test_unresolved_future_window_never_starts_network_after_budget_exhaustion(monkeypatch):
    discovery = Mock(side_effect=AssertionError('must not discover outside shared budget'))
    calendar = Mock(side_effect=AssertionError('must not load a calendar outside shared budget'))
    monkeypatch.setattr(jiangsu_fuel, '_discover_next_window', discovery)
    monkeypatch.setattr(jiangsu_fuel, '_next_calculated_window', calendar)
    alert = _collect_fuel_alert(budget=RuntimeBudget(0), today=date(2027, 1, 6), fred_api_key='', on_timeout=lambda alert: alert)
    assert alert is None
    discovery.assert_not_called()
    calendar.assert_not_called()


@pytest.mark.parametrize('instant,visible', [
    ('2026-10-15T00:00:00+08:00', True), ('2026-10-15T23:59:59+08:00', True),
    ('2026-10-15T15:59:59+00:00', True), ('2026-10-15T16:00:00+00:00', False),
])
def test_timeout_fallback_respects_beijing_effective_midnight(instant, visible):
    now = to_beijing(datetime.fromisoformat(instant))
    alert = _collect_fuel_alert(budget=RuntimeBudget(0), today=now.date(), fred_api_key='', on_timeout=lambda alert: alert)
    assert (alert is not None) == visible
    html = render_email(signals=[], generated_at=now, jiangsu_fuel_alert=alert)
    assert ('油价预告' in html) == visible


def test_successful_verified_prediction_is_not_replaced_by_schedule(monkeypatch):
    from tests.test_jiangsu_fuel import _entry
    monkeypatch.setattr(jiangsu_fuel, '_fetch_forecast_entries', lambda _: [
        _entry('10月15日24时，预计92号汽油每升下调0.18元', published='2026-10-14 03:00:00')])
    degraded = Mock(side_effect=AssertionError('successful result must not degrade'))
    alert = _collect_fuel_alert(budget=RuntimeBudget(1), today=date(2026, 10, 15), fred_api_key='', on_timeout=degraded)
    assert alert and alert.direction == '下调' and '0.18' in alert.detail
    assert alert.forecast_method == 'news' and alert.observed_at == '2026-10-14'
    degraded.assert_not_called()
