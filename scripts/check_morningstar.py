"""Verify public Morningstar sources without model calls or sending any email."""

import argparse
import logging
from datetime import UTC, datetime
from pathlib import Path

from src.valuation.morningstar import MorningstarPublicProvider, refresh_fair_values


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--state-dir",
        type=Path,
        required=True,
        help="Isolated output directory for verified snapshots and diagnostics",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    values, failures = refresh_fair_values(
        provider=MorningstarPublicProvider(timeout=12),
        state_dir=args.state_dir,
        prices={},
        checked_at=datetime.now(UTC),
    )
    verified = carried = excluded = 0
    for ticker, value in values.items():
        if value.historical_only:
            excluded += 1
            print(f"{ticker}: unverified legacy record excluded from display and IRR")
            continue
        verified += 1
        carried += int(value.stale_cache)
        print(
            f"{ticker}: {value.fair_value:g} {value.currency}; report={value.fair_value_updated_at}; "
            f"verified={value.retrieved_at}; carried={value.stale_cache}"
        )
    print(
        f"Verified={verified}; live={verified - carried}; carried={carried}; "
        f"excluded={excluded}; issues={failures}"
    )


if __name__ == "__main__":
    main()
