"""Merge isolated broker-target recovery with daily state and the verified seed."""

from datetime import UTC, datetime
from pathlib import Path

from src.valuation.pop_mart import load_target, save_target


def normalize(state_dir: Path, config_dir: Path, *, checked_at: datetime) -> bool:
    value = load_target(state_dir=state_dir, config_dir=config_dir, checked_at=checked_at)
    if value is None:
        return False
    save_target(value, state_dir=state_dir)
    return True


if __name__ == "__main__":
    if not normalize(Path("state"), Path("config"), checked_at=datetime.now(UTC)):
        raise SystemExit("No verified Pop Mart broker target available")
    print("Pop Mart broker target history validated; no publication state touched")
