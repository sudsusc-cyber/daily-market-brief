"""Preserve and merge valuation-only state; never touches publication/dedup state."""

import argparse
import logging
from datetime import UTC, datetime
from pathlib import Path

from src.valuation.morningstar import _save_cache, load_verified_values


def normalize(state_dir: Path, config_dir: Path, *, checked_at: datetime) -> int:
    values = load_verified_values(
        state_dir=state_dir, checked_at=checked_at, prices={},
        baseline_path=config_dir / "morningstar_verified_snapshot.json",
    )
    if values:
        for name in ("morningstar_last_verified.json", "morningstar_fair_values.json"):
            try:
                _save_cache(state_dir / name, values)
            except OSError as exc:
                logging.warning("Could not persist %s: %s", name, exc)
    return len(values)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("preserve", "normalize"))
    parser.parse_args()
    count = normalize(Path("state"), Path("config"), checked_at=datetime.now(UTC))
    print(f"Morningstar retained history: {count} securities")


if __name__ == "__main__":
    main()
