"""Poetic openings must not invent holding counts or trade-trigger facts."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.processors.holdings_intro import write_intro

SIGNALS=[SimpleNamespace(signal='DCA',error=None,holding=SimpleNamespace(ticker='0700.HK'),
                         buy_lines=[],last_close=439.8)]
GOOD='潮水有起落，价格也有自己的节律；守候的人不急于追逐远帆，只在熟悉的岸边辨认水位。'


@pytest.mark.parametrize('bad', [
    '海面之上，十一艘船正悬于高位，而香江之畔，两叶轻舟悄然退至潮线之下。',
    '十三只股票都在高位。',
    '价格距离参考线还有15%。',
    '所有标的以120周和200周触发信号。',
    '全部标的都无信号。',
    '港股首次跌破参考线，建议加仓。',
    '这是开场白。'*30,
])
def test_unsupported_intro_retries_with_same_inputs_and_keeps_verified_replacement(bad):
    client=Mock()
    client.chat.side_effect=[SimpleNamespace(text=bad,error=None),SimpleNamespace(text=GOOD,error=None)]
    assert write_intro(SIGNALS,client=client)==GOOD
    assert client.chat.call_count==2
    assert client.chat.call_args_list[0].args==client.chat.call_args_list[1].args


def test_second_unsupported_intro_returns_controlled_fallback_not_truncated_claim():
    client=Mock()
    client.chat.return_value=SimpleNamespace(text='十一艘船都高于参考线。',error=None)
    assert write_intro(SIGNALS,client=client) is None
    assert client.chat.call_count==2
