"""Readable report notes without changing valuation or provenance records."""

from __future__ import annotations

import re
from datetime import datetime

from src.utils.dates import to_beijing
from src.valuation.models import ValuationDisplay

_REPORT_NOTE = re.compile(
    r'(?P<subject>.+?)\s*(?P<action>沿用|采用|来源分歧，采用)\s*'
    r'(?P<date>\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?)?)'
    r'\s*(?P<kind>报告值|报告目标价)(?:（(?P<detail>[^）]*)）)?'
)


def _report_day(value: str) -> str | None:
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    # Report dates are source dates. Never substitute retrieval/check time.
    return (to_beijing(stamp) if stamp.tzinfo else stamp).date().isoformat()


def valuation_notes(valuations: dict[str, ValuationDisplay]) -> dict:
    """Merge only recognized notes for the same subject and report day.

    Unrecognized clauses and different reports remain visible. The returned
    dates let the source legend avoid repeating a report date already in a note.
    Raw notes, warnings, and source timestamps remain unchanged for the audit.
    """
    items: list[str] = []
    report_dates: dict[str, str] = {}
    for ticker, value in valuations.items():
        if not value.data_note:
            continue
        clauses = re.split(r'[；;](?![^（]*）)', value.data_note.strip().rstrip('。'))
        groups: dict[tuple[str, str], dict] = {}
        ordered: list[str | tuple[str, str]] = []
        for clause in clauses:
            clause = clause.strip()
            match = _REPORT_NOTE.fullmatch(clause)
            day = _report_day(match['date']) if match else None
            if not match or not day:
                if clause and clause not in ordered:
                    ordered.append(clause)
                continue
            key = (match['subject'].strip(), day)
            if key not in groups:
                groups[key] = {'retained': False, 'kind': '报告值', 'details': []}
                ordered.append(key)
            group = groups[key]
            group['retained'] |= match['action'] == '沿用'
            if match['kind'] == '报告目标价':
                group['kind'] = match['kind']
            details = ([match['detail']] if match['detail'] else [])
            if '来源分歧' in match['action']:
                details.append('来源分歧')
            for detail in details:
                if detail not in group['details']:
                    group['details'].append(detail)
            if day == value.financial_as_of:
                report_dates[ticker] = day
        for entry in ordered:
            if isinstance(entry, str):
                items.append(entry)
                continue
            subject, day = entry
            group = groups[entry]
            action = '沿用' if group['retained'] else '采用'
            detail = '；'.join(group['details'])
            separator = ' ' if subject[-1].isascii() else ''
            items.append(f"{subject}{separator}{action} {day} {group['kind']}" + (f'（{detail}）' if detail else ''))
    return {'items': items, 'report_dates': report_dates}
