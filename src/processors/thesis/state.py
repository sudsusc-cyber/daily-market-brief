"""
Thesis state 持久化 IO。

- thesis_state.json: 原子写入（.tmp → os.replace）
- thesis_evidence_YYYY.jsonl: 按年滚动，原子写入 + 按 evidence_id 去重
- load_recent_evidence: 读取最近 N 天，支持跨年 + 去重
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .models import ThesisEvidence, ThesisState

logger = logging.getLogger("thesis.state")


def _state_path(state_dir: Path) -> Path:
    return state_dir / "thesis_state.json"


def _evidence_path(state_dir: Path, year: int) -> Path:
    return state_dir / f"thesis_evidence_{year}.jsonl"


# ─── thesis_state.json ────────────────────────────────────────────


def load_state(state_dir: Path) -> dict[str, ThesisState]:
    """读取 thesis_state.json。损坏 / 不存在 → 返回空 dict + warning。"""
    path = _state_path(state_dir)
    if not path.exists():
        logger.info("state.not_found path=%s", path)
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("state.corrupted path=%s exc=%r", path, exc)
        return {}
    result: dict[str, ThesisState] = {}
    for theme, obj in raw.items():
        try:
            result[theme] = _dict_to_state(obj)
        except Exception as exc:  # noqa: BLE001
            logger.warning("state.bad_entry theme=%s exc=%r", theme, exc)
    return result


def save_state(state: dict[str, ThesisState], state_dir: Path) -> None:
    """原子写入 thesis_state.json。"""
    path = _state_path(state_dir)
    tmp = path.with_suffix(".tmp")
    obj = {theme: _state_to_dict(st) for theme, st in state.items()}
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    logger.info("state.saved themes=%d path=%s", len(state), path)


# ─── thesis_evidence_YYYY.jsonl ───────────────────────────────────


def append_evidence(
    evidence_list: list[ThesisEvidence],
    state_dir: Path,
    *,
    today: date | None = None,
) -> int:
    """原子追加 evidence 到当年 jsonl，内部按 evidence_id 去重。返回新写入条数。"""
    if not evidence_list:
        return 0
    if today is None:
        today = date.today()
    path = _evidence_path(state_dir, today.year)

    # 1) 读现有：逐行解析，保留有效行 + 收集 evidence_id；坏行自动丢弃
    existing_ids: set[str] = set()
    existing_lines: list[str] = []
    if path.exists():
        try:
            for raw in path.read_text(encoding="utf-8").splitlines():
                line = raw.rstrip("\n")
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("evidence.bad_json_line path=%s preview=%s",
                                   path, line[:80])
                    continue
                eid = obj.get("evidence_id", "")
                if eid:
                    existing_ids.add(eid)
                    existing_lines.append(line)
                else:
                    # 缺 evidence_id 的旧行：保留但不计入 dedup（防御性）
                    existing_lines.append(line)
        except OSError:
            pass

    # 2) 过滤 items：跳过已存在 + 同批次去重
    seen_in_batch: set[str] = set()
    new_lines: list[str] = []
    for e in evidence_list:
        if not e.evidence_id:
            logger.warning("evidence.missing_id theme=%s date=%s", e.theme, e.date)
            continue
        if e.evidence_id in existing_ids or e.evidence_id in seen_in_batch:
            logger.debug("evidence.duplicate id=%s theme=%s", e.evidence_id, e.theme)
            continue
        seen_in_batch.add(e.evidence_id)
        new_lines.append(json.dumps(_evidence_to_dict(e), ensure_ascii=False))

    if not new_lines:
        logger.info("evidence.no_new total=%d", len(evidence_list))
        return 0

    # 3) 写 tmp：已有有效行 + 新行
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for line in existing_lines:
            f.write(line + "\n")
        for line in new_lines:
            f.write(line + "\n")

    # 4) 原子替换
    os.replace(tmp, path)

    logger.info("evidence.appended count=%d path=%s", len(new_lines), path)
    return len(new_lines)


def load_recent_evidence(
    state_dir: Path,
    *,
    days: int = 90,
    today: date | None = None,
) -> list[ThesisEvidence]:
    """读取最近 `days` 天 evidence，跨年 + 按 evidence_id 去重。"""
    if today is None:
        today = date.today()
    cutoff = today - timedelta(days=days)
    result: list[ThesisEvidence] = []
    seen_ids: set[str] = set()

    years_to_check = {today.year}
    if cutoff.year < today.year:
        years_to_check.add(today.year - 1)

    for year in sorted(years_to_check):
        path = _evidence_path(state_dir, year)
        if not path.exists():
            continue
        try:
            for line in path.read_text(encoding="utf-8").strip().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("evidence.bad_json_line path=%s preview=%s",
                                   path, line[:80])
                    continue
                ev_date = obj.get("date", "")
                if ev_date < cutoff.isoformat():
                    continue
                eid = obj.get("evidence_id", "")
                if eid and eid in seen_ids:
                    continue
                if eid:
                    seen_ids.add(eid)
                try:
                    result.append(_dict_to_evidence(obj))
                except Exception as exc:  # noqa: BLE001
                    logger.warning("evidence.bad_line exc=%r", exc)
        except OSError as exc:
            logger.warning("evidence.read_error path=%s exc=%r", path, exc)

    return result


def load_all_evidence(state_dir: Path) -> list[ThesisEvidence]:
    """读取账本中全部年份的 evidence，按 evidence_id 去重。"""
    result: list[ThesisEvidence] = []
    seen_ids: set[str] = set()
    for path in sorted(state_dir.glob("thesis_evidence_*.jsonl")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            logger.warning("evidence.read_error path=%s exc=%r", path, exc)
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
                ev = _dict_to_evidence(obj)
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                logger.warning("evidence.bad_line path=%s exc=%r", path, exc)
                continue
            if ev.evidence_id and ev.evidence_id in seen_ids:
                continue
            if ev.evidence_id:
                seen_ids.add(ev.evidence_id)
            result.append(ev)
    return result


def replace_all_evidence(
    evidence_list: list[ThesisEvidence],
    state_dir: Path,
) -> None:
    """按年原子重写 evidence 账本，用于版本化主题迁移。"""
    state_dir.mkdir(parents=True, exist_ok=True)
    by_year: dict[int, list[ThesisEvidence]] = {}
    seen_ids: set[str] = set()
    for ev in sorted(evidence_list, key=lambda item: (item.date, item.evidence_id)):
        try:
            year = date.fromisoformat(ev.date).year
        except ValueError:
            logger.warning("evidence.skip_bad_date date=%r theme=%s", ev.date, ev.theme)
            continue
        if ev.evidence_id and ev.evidence_id in seen_ids:
            continue
        if ev.evidence_id:
            seen_ids.add(ev.evidence_id)
        by_year.setdefault(year, []).append(ev)

    existing_years: set[int] = set()
    for path in state_dir.glob("thesis_evidence_*.jsonl"):
        try:
            existing_years.add(int(path.stem.rsplit("_", 1)[-1]))
        except ValueError:
            continue

    for year in sorted(existing_years | set(by_year)):
        path = _evidence_path(state_dir, year)
        tmp = path.with_suffix(".tmp")
        rows = by_year.get(year, [])
        with tmp.open("w", encoding="utf-8") as handle:
            for ev in rows:
                handle.write(json.dumps(_evidence_to_dict(ev), ensure_ascii=False) + "\n")
        os.replace(tmp, path)
        logger.info("evidence.replaced year=%d count=%d path=%s", year, len(rows), path)


# ─── rolling evidence 维护 ─────────────────────────────────────────


def update_rolling_evidence(
    state: ThesisState,
    new_evidence: list[ThesisEvidence],
    max_items: int = 20,
) -> None:
    """把新 evidence 的摘要追加到 rolling_evidence，保持最近 max_items 条。"""
    existing_ids = {
        str(item.get("evidence_id", ""))
        for item in state.rolling_evidence
        if isinstance(item, dict) and item.get("evidence_id")
    }
    for e in new_evidence:
        if e.evidence_id and e.evidence_id in existing_ids:
            continue
        state.rolling_evidence.append({
            "evidence_id": e.evidence_id,
            "date": e.date,
            "source_section": e.source_section,
            "source_name": e.source_name,
            "direction": e.direction,
            "strength": e.strength,
            "text": e.text[:200],
        })
        if e.evidence_id:
            existing_ids.add(e.evidence_id)
    if len(state.rolling_evidence) > max_items:
        state.rolling_evidence = state.rolling_evidence[-max_items:]


# ─── dict ↔ dataclass 序列化 ──────────────────────────────────────


def _evidence_to_dict(e: ThesisEvidence) -> dict[str, Any]:
    return {
        "evidence_id": e.evidence_id,
        "date": e.date,
        "source_section": e.source_section,
        "source_name": e.source_name,
        "url": e.url,
        "related_tickers": e.related_tickers,
        "theme": e.theme,
        "direction": e.direction,
        "strength": e.strength,
        "horizon": e.horizon,
        "text": e.text,
        "why_it_matters": e.why_it_matters,
    }


def _dict_to_evidence(obj: dict[str, Any]) -> ThesisEvidence:
    return ThesisEvidence(
        evidence_id=obj.get("evidence_id", ""),
        date=obj["date"],
        source_section=obj.get("source_section", ""),
        source_name=obj.get("source_name", ""),
        url=obj.get("url"),
        related_tickers=list(obj.get("related_tickers", [])),
        theme=obj.get("theme", ""),
        direction=obj.get("direction", "neutral"),
        strength=int(obj.get("strength", 3)),
        horizon=obj.get("horizon", "quarterly"),
        text=obj.get("text", ""),
        why_it_matters=obj.get("why_it_matters", ""),
    )


def _state_to_dict(st: ThesisState) -> dict[str, Any]:
    return {
        "theme": st.theme,
        "status": st.status,
        "related_tickers": st.related_tickers,
        "cadence": st.cadence,
        "stale_after_days": st.stale_after_days,
        "first_seen": st.first_seen,
        "last_evidence_date": st.last_evidence_date,
        "last_strong_evidence_date": st.last_strong_evidence_date,
        "last_displayed_date": st.last_displayed_date,
        "evidence_count_total": st.evidence_count_total,
        "evidence_count_recent_90d": st.evidence_count_recent_90d,
        "rolling_evidence": st.rolling_evidence,
        "one_line_thesis": st.one_line_thesis,
        "last_state_change_date": st.last_state_change_date,
    }


def _dict_to_state(obj: dict[str, Any]) -> ThesisState:
    return ThesisState(
        theme=obj["theme"],
        status=obj.get("status", "candidate"),
        related_tickers=list(obj.get("related_tickers", [])),
        cadence=obj.get("cadence", "quarterly"),
        stale_after_days=int(obj.get("stale_after_days", 180)),
        first_seen=obj.get("first_seen", ""),
        last_evidence_date=obj.get("last_evidence_date", ""),
        last_strong_evidence_date=obj.get("last_strong_evidence_date"),
        last_displayed_date=obj.get("last_displayed_date"),
        evidence_count_total=int(obj.get("evidence_count_total", 0)),
        evidence_count_recent_90d=int(obj.get("evidence_count_recent_90d", 0)),
        rolling_evidence=list(obj.get("rolling_evidence", [])),
        one_line_thesis=obj.get("one_line_thesis", ""),
        last_state_change_date=obj.get("last_state_change_date"),
    )
