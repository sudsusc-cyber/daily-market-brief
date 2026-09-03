"""Keep both daily and independent snapshots, and publish only verified inputs."""

from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

from src.valuation.qqqm import _from_cache, cache_paths, daily_forward_enabled


def preserve_daily(state_dir: Path) -> None:
    primary = state_dir / "qqqm_valuation.json"
    try:
        if primary.is_file():
            (state_dir / "qqqm_valuation.daily.json").write_bytes(primary.read_bytes())
    except OSError as exc:
        logging.warning("QQQM daily snapshot preservation failed: %s", type(exc).__name__)


def normalize_cache(state_dir: Path, *, checked_at: datetime, allow_daily_forward: bool) -> bool:
    candidates = []
    for path in cache_paths(state_dir):
        result = _from_cache(path, price=1.0, checked_at=checked_at,
                             allow_daily_forward=allow_daily_forward)
        if result is not None:
            candidates.append((result.inputs.data_date, result.inputs.fwd_date or "", path))
    if not candidates:
        return False
    source = max(candidates)[2]
    # Keep the original observation dates; a new cache key is NOT fresh data.
    payload = json.loads(source.read_text(encoding="utf-8"))
    state_dir.mkdir(parents=True, exist_ok=True)
    target = state_dir / "qqqm_valuation.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(target)
    return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("preserve", "normalize"))
    args = parser.parse_args()
    if args.mode == "preserve":
        preserve_daily(Path("state"))
    else:
        valid = normalize_cache(Path("state"), checked_at=datetime.now(UTC),
                                allow_daily_forward=daily_forward_enabled())
        print(f"QQQM verified cache valid={valid}")
        if output := os.environ.get("GITHUB_OUTPUT"):
            with Path(output).open("a", encoding="utf-8") as stream:
                stream.write(f"valid={str(valid).lower()}\n")


if __name__ == "__main__":
    main()
