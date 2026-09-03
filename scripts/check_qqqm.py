"""QQQM production-source diagnostic; never sends email."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from src.collectors import stocks
from src.config import HOLDINGS
from src.processors.llm_client import LLMClient
from src.valuation.qqqm import prepare_qqqm_display


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    client = LLMClient(api_key=os.environ["DEEPSEEK_API_KEY"], total_timeout_seconds=240)
    final_text: list[str | None] = []
    original_search = client.search_web

    def record(*args, **kwargs):
        response = original_search(*args, **kwargs)
        final_text.append(response.text)
        return response

    client.search_web = record
    signal = stocks.fetch_all([h for h in HOLDINGS if h.ticker == "QQQM"])[0]
    if not signal.last_close:
        raise ValueError("QQQM market price unavailable")
    display = prepare_qqqm_display(
        price=signal.last_close, client=client, state_dir=Path("state"),
        checked_at=datetime.now(UTC),
    )
    report = {"display": asdict(display), "final_text": final_text, "usage": asdict(client.cumulative)}
    Path("state").mkdir(exist_ok=True)
    Path("state/qqqm_diagnostic.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(asdict(display), ensure_ascii=False))
    return 1 if display.is_pending else 0


if __name__ == "__main__":
    raise SystemExit(main())
