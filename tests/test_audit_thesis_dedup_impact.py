"""test_audit_thesis_dedup_impact.py — 审计脚本 dedupe_evidence 单元测试"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from audit_thesis_dedup_impact import dedupe_evidence

from src.processors.thesis.models import ThesisEvidence


def _ev(date_str="2026-05-04", theme="test-theme", text="test text") -> ThesisEvidence:
    return ThesisEvidence(
        evidence_id=f"id-{theme}-{date_str}-{hash(text) & 0xFFFF:04x}",
        date=date_str,
        source_section="company_news",
        source_name="Reuters",
        url=None,
        related_tickers=["TEST"],
        theme=theme,
        direction="support",
        strength=4,
        horizon="multi_year",
        text=text,
        why_it_matters="test reason",
    )


def test_dedupe_evidence_collapses_similar():
    """相似度 ≥ 0.72 的文本被合并为 1 条。"""
    evs = [
        _ev("2026-05-01", text="美联储加息25个基点的决定引发市场广泛关注"),
        _ev("2026-05-02", text="美联储加息25个基点决定引发市场广泛关注"),
        _ev("2026-05-03", text="美联储加息25个基点的决策引发市场广泛关注"),
    ]
    # 给每条不同的 evidence_id 避免 hash 碰撞
    for i, e in enumerate(evs):
        e.evidence_id = f"similar-test-{i}"

    result = dedupe_evidence(evs)
    assert len(result) == 1


def test_dedupe_evidence_keeps_unrelated_apart():
    """完全不同事件的 evidence 全部保留。"""
    evs = [
        _ev("2026-05-01", text="美联储宣布维持利率不变"),
        _ev("2026-05-02", text="苹果发布新一代MacBook Pro产品线"),
        _ev("2026-05-03", text="英伟达数据中心业务营收同比增长超过两倍"),
    ]
    for i, e in enumerate(evs):
        e.evidence_id = f"unrelated-test-{i}"

    result = dedupe_evidence(evs)
    assert len(result) == 3


def test_dedupe_evidence_respects_threshold():
    """threshold 参数控制敏感性：高阈值时宽松，低阈值时严格。"""
    evs = [
        _ev("2026-05-01", text="美联储加息25个基点的决定引发市场关注"),
        _ev("2026-05-02", text="美联储加息25个基点决定引发市场关注"),
        _ev("2026-05-03", text="欧洲央行维持利率不变并发布鸽派声明"),
    ]
    for i, e in enumerate(evs):
        e.evidence_id = f"threshold-test-{i}"

    # threshold=0.95: 前两条也不够相似，三条全保留
    result_strict = dedupe_evidence(evs, threshold=0.95)
    assert len(result_strict) >= 2

    # threshold=0.6: 前两条合并，第三条保留
    result_loose = dedupe_evidence(evs, threshold=0.6)
    assert len(result_loose) == 2


def test_dedupe_evidence_keeps_earliest():
    """去重时保留 date 最早的 evidence。"""
    evs = [
        _ev("2026-05-03", text="美联储宣布加息决定"),
        _ev("2026-05-01", text="美联储宣布加息决定"),
        _ev("2026-05-02", text="美联储宣布加息决定"),
    ]
    for i, e in enumerate(evs):
        e.evidence_id = f"earliest-test-{i}"

    result = dedupe_evidence(evs)
    assert len(result) == 1
    assert result[0].date == "2026-05-01"


def test_dedupe_evidence_empty():
    """空列表返回空列表。"""
    assert dedupe_evidence([]) == []


def test_dedupe_evidence_single():
    """单条返回单条。"""
    ev = _ev()
    result = dedupe_evidence([ev])
    assert len(result) == 1
    assert result[0] is ev
