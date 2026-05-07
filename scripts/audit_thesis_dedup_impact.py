"""
评估"如果去重从 Day 1 就开启，当前 thesis_state 会变成什么样"。

只读 state/thesis_evidence_*.jsonl + thesis_state.json，
对每个 theme 的 evidence 做 SequenceMatcher 0.72 模糊去重，
模拟 rules.run_state_transitions 跑一遍，
对比当前 vs 模拟得到差异，写 Markdown 报告。

不修改任何 state 文件。

运行方式:
    uv run python scripts/audit_thesis_dedup_impact.py
"""

from __future__ import annotations

import copy
import re
import sys
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.processors.thesis.models import ThesisEvidence
from src.processors.thesis.rules import run_state_transitions
from src.processors.thesis.state import load_recent_evidence, load_state

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_STATE_DIR = _PROJECT_ROOT / "state"
_AUDITS_DIR = _PROJECT_ROOT / "docs" / "audits"


def _normalize(text: str) -> str:
    text = re.sub(r"[^\w一-鿿]", "", text, flags=re.UNICODE)
    return text.lower()


def _similar(a: str, b: str, *, threshold: float = 0.72) -> bool:
    return SequenceMatcher(None, _normalize(a), _normalize(b)).ratio() >= threshold


def dedupe_evidence(
    evidence_list: list[ThesisEvidence],
    *,
    threshold: float = 0.72,
) -> list[ThesisEvidence]:
    """对一组 theme 内的 evidence 模糊去重，保留首个（date 最早）。"""
    sorted_evs = sorted(evidence_list, key=lambda e: e.date)
    kept: list[ThesisEvidence] = []
    for ev in sorted_evs:
        if any(_similar(ev.text, k.text, threshold=threshold) for k in kept):
            continue
        kept.append(ev)
    return kept


def count_90d(
    evidence_list: list[ThesisEvidence],
    today: date,
) -> int:
    """统计 90 天窗口内的 evidence 数（不去重）。"""
    from datetime import timedelta

    cutoff = today - timedelta(days=90)
    return sum(1 for e in evidence_list if e.date >= cutoff.isoformat())


def main() -> int:
    today = date.today()
    current_state = load_state(_STATE_DIR)
    full_evidence = load_recent_evidence(_STATE_DIR, days=365, today=today)

    # 按 theme 分组
    by_theme: dict[str, list[ThesisEvidence]] = {}
    for ev in full_evidence:
        by_theme.setdefault(ev.theme, []).append(ev)

    # 对每个 theme 做模糊去重
    deduped_by_theme: dict[str, list[ThesisEvidence]] = {
        theme: dedupe_evidence(evs) for theme, evs in by_theme.items()
    }

    # 当前 90d 计数 vs 去重后 90d 计数
    cur_90d: dict[str, int] = {}
    sim_90d: dict[str, int] = {}
    for theme, evs in by_theme.items():
        cur_90d[theme] = count_90d(evs, today)
    for theme, evs in deduped_by_theme.items():
        sim_90d[theme] = count_90d(evs, today)

    # 模拟 state transitions：深拷贝当前 state，用去重后的 evidence 跑
    simulated_state = copy.deepcopy(current_state)
    deduped_flat = [ev for evs in deduped_by_theme.values() for ev in evs]
    simulated_state, _ = run_state_transitions(
        today=today,
        state=simulated_state,
        recent_evidence=deduped_flat,
    )

    # 收集所有 theme
    all_themes = sorted(set(current_state.keys()) | set(simulated_state.keys()))

    # 生成行数据
    rows: list[dict] = []
    for theme in all_themes:
        cur = current_state.get(theme)
        sim = simulated_state.get(theme)
        cur_status = cur.status if cur else "—"
        sim_status = sim.status if sim else "—"
        cc = cur_90d.get(theme, 0)
        sc = sim_90d.get(theme, 0)
        risk = "安全" if cur_status == sim_status and cc == sc else "⚠️ 可能降级"
        rows.append({
            "theme": theme,
            "cur_status": cur_status,
            "sim_status": sim_status,
            "cur_90d": cc,
            "sim_90d": sc,
            "risk": risk,
        })

    # 写 Markdown 报告
    _AUDITS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = _AUDITS_DIR / f"{today.isoformat()}-thesis-dedup-impact.md"
    with out_path.open("w", encoding="utf-8") as f:
        f.write(f"# Thesis 去重影响审计 · {today.isoformat()}\n\n")
        f.write("| theme | 当前 status | 模拟 status | 当前 90d evidence | 去重后 90d evidence | 风险 |\n")
        f.write("|-------|-------------|-------------|--------------------|---------------------|------|\n")
        for r in rows:
            f.write(
                f"| {r['theme']} | {r['cur_status']} | {r['sim_status']} "
                f"| {r['cur_90d']} | {r['sim_90d']} | {r['risk']} |\n"
            )

    print(f"Audit report written to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
