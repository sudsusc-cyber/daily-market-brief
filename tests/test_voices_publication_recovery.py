from datetime import UTC, datetime
from types import SimpleNamespace

from src.collectors.figures import FigureMention
from src.processors.figure_filter import _recover_selected, _RecoveryBudget


def test_recovery_retries_only_unresolved_selected_sources(monkeypatch):
    items = [FigureMention(f'Alex says revenue increased by {n}%.', '', datetime.now(UTC), f'https://example.com/{n}', 'Source') for n in (10,20)]
    calls=[]
    def translate(pending, **kwargs):
        calls.append(list(pending))
        for item in pending:
            item.source_excerpt=item.title
            if len(calls)>1 or item is items[0]:
                n=10 if item is items[0] else 20
                item.translated_excerpt=f'Alex 表示收入增长 {n}%。'
            else:
                item.translated_excerpt=''
    monkeypatch.setattr('src.processors.translator.translate_in_place_news',translate)
    budget=_RecoveryBudget()
    audit=[]
    parsed=SimpleNamespace(rejected_indexes={1:'translation_unavailable',2:'translation_unavailable'})
    assert _recover_selected(items,parsed,client=object(),budget=budget,audit=audit)
    assert calls == [items,[items[1]]]
    assert budget.calls == 0
    assert audit[0]['recovered'] == 2
    assert len(audit[0]['attempts']) == 2


def test_exhaustion_is_bounded_and_preserves_all_attempt_diagnostics(monkeypatch):
    source=FigureMention('Alex says revenue increased by 10%.','',datetime.now(UTC),'https://example.com/x','Source')
    def fail(*args,**kwargs):
        raise TimeoutError('temporary failure')
    monkeypatch.setattr('src.processors.translator.translate_in_place_news',fail)
    budget=_RecoveryBudget()
    audit=[]
    parsed=SimpleNamespace(rejected_indexes={1:'translation_unavailable'})
    assert _recover_selected([source],parsed,client=object(),budget=budget,audit=audit)
    assert budget.calls == 0 and audit[0]['recovered'] == 0
    assert all('TimeoutError' in a['error'] for a in audit[0]['attempts'])
    assert not _recover_selected([source],parsed,client=object(),budget=budget,audit=audit)



def test_translation_repair_gets_failed_wording_and_still_checks_source():
    from src.processors.translator import translate_titles

    calls=[]
    responses=iter(['▦ 1: Alex 表示公司已投入 100 万美元。', '▦ 1: Alex 表示公司将投入 100 万美元。'])
    def chat(payload, **kwargs):
        calls.append(payload)
        return SimpleNamespace(text=next(responses),error=None)
    original='Alex says the company will spend $1 million.'
    result=translate_titles([original],client=SimpleNamespace(chat=chat),diagnostics={})
    assert result == ['Alex 表示公司将投入 100 万美元。']
    assert len(calls)==2
    assert '已投入' in calls[1] and '区分计划' in calls[1]
    assert original in calls[1]


def test_real_selected_voice_recovers_on_second_shared_attempt():
    from src.collectors.figures import FigureBundle
    from src.processors.figure_filter import filter_one
    from src.processors.thesis.extractor import _verified_grounding_row

    original='Jensen Huang says Nvidia is not intimidated by custom AI chips.'
    source=FigureMention(original,'',datetime.now(UTC),'https://example.com/voice','Source')
    responses=iter(['▦ 1: yes | score=5 | 发言有具体竞争判断',
                    '▦ 1: Jensen Huang 称 Nvidia 畏惧定制 AI 芯片。',
                    '▦ 1: Jensen Huang 称 Nvidia 不惧定制 AI 芯片。'])
    client=SimpleNamespace(chat=lambda *args,**kwargs:SimpleNamespace(text=next(responses),error=None))
    result=filter_one(FigureBundle('黄仁勋','query',items=[source]),client=client)
    assert result.items and not result.error
    assert '不惧' in result.items[0].text
    assert _verified_grounding_row(result.items[0],result.items[0].evidence[0])
    assert result.verification_audit[0]['rejected_indexes']
    recovery=result.verification_audit[1]
    assert recovery['attempts'][0]['after'][0]['errors']
    assert recovery['attempts'][1]['after'][0]['errors'] == []
