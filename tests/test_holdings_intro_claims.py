"""Actual signal states determine every factual clause of the opening."""
from types import SimpleNamespace
from unittest.mock import Mock

from src.processors.holdings_intro import signal_context, write_intro


def test_actual_below_reference_none_and_two_hk_positions_cannot_invent_claims():
    signals = [SimpleNamespace(signal='NONE', error=None, last_close=922.92, sma_120=945.82)]
    signals += [SimpleNamespace(signal='DCA',error=None) for _ in range(2)]
    client=Mock()
    client.chat.return_value=SimpleNamespace(text='两地各有一处，其余均在参考线之上。',error=None)
    text=write_intro(signals,client=client)
    assert text is None
    assert signal_context(signals) == '持仓信号有所分化，部分标的处于既定买入区间。'
    assert client.chat.call_count == 2


def test_failed_and_unknown_observations_are_not_counted_as_no_signal():
    signals = [SimpleNamespace(signal='NONE',error='missing'),
               SimpleNamespace(signal='unexpected',error=None),
               SimpleNamespace(signal='LUMP_SUM',error=None)]
    text=signal_context(signals)
    assert '待核验' in text
    assert '暂无买入信号' not in text
