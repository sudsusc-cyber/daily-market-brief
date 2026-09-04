"""Install an independently verified startup snapshot, never an estimated PE.

Only the manual workflow exposes this input. Regular runs always try live search
first. The seed must agree with the current official NAV/dividend source packet,
and all usual source/date/value gates still apply when reading its cache later.
"""

from __future__ import annotations

import json
import math
import os
from datetime import UTC, datetime
from pathlib import Path

from src.valuation.qqqm import (
    calculate_qqqm,
    daily_forward_enabled,
    parse_qqqm_inputs,
    snapshot_payload,
)
from src.valuation.qqqm_sources import fetch_source_packet


def seed_snapshot(text: str, *, packet: dict, state_dir: Path, checked_at: datetime) -> None:
    inputs = parse_qqqm_inputs(text, price=packet["nav_anchor"], checked_at=checked_at,
                               allow_daily_forward=daily_forward_enabled())
    fields = ["nav_anchor", "div_ttm", "data_date", "pe_pair_t", "pe_pair_f", "fwd_date"]
    fields.extend(key for key in ("pe_ttm", "forward_basis") if packet.get(key) is not None)
    for field in fields:
        expected, actual = packet[field], getattr(inputs, field)
        if isinstance(expected, float) and isinstance(actual, (float, int)):
            same = math.isclose(expected, actual, rel_tol=1e-8)
        else:
            same = expected == actual
        if not same:
            raise ValueError(f"QQQM seed differs from live source: {field}")
    result = calculate_qqqm(inputs)
    state_dir.mkdir(parents=True, exist_ok=True)
    target = state_dir / "qqqm_valuation.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(snapshot_payload(result, source_response=text, checked_at=checked_at),
                                    ensure_ascii=False))
    temporary.replace(target)


def main() -> None:
    checked_at = datetime.now(UTC)
    packet = fetch_source_packet(checked_at=checked_at, allow_daily_forward=daily_forward_enabled())
    if packet is None:
        raise ValueError("Live official sources unavailable; do not install seed")
    seed_snapshot(os.environ["QQQM_VERIFIED_SEED"], packet=packet,
                  state_dir=Path("state"), checked_at=checked_at)
    print(f"QQQM independently verified startup snapshot installed: {packet['data_date']}")


if __name__ == "__main__":
    main()
