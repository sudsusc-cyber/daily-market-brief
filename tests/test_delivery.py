from __future__ import annotations

import json
from datetime import UTC, datetime

from src.utils.delivery import clear_delivery_receipt, write_delivery_receipt


def test_delivery_receipt_written_only_when_configured(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DELIVERY_RECEIPT_PATH", raising=False)
    write_delivery_receipt(sent_at=datetime(2026, 7, 11, tzinfo=UTC), run_id="123")
    assert list(tmp_path.iterdir()) == []

    path = tmp_path / "receipt.json"
    monkeypatch.setenv("DELIVERY_RECEIPT_PATH", str(path))
    write_delivery_receipt(sent_at=datetime(2026, 7, 11, tzinfo=UTC), run_id="123")

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload == {"run_id": "123", "sent_at": "2026-07-11T00:00:00+00:00"}


def test_clear_delivery_receipt_removes_stale_file(tmp_path, monkeypatch) -> None:
    path = tmp_path / "receipt.json"
    path.write_text("stale", encoding="utf-8")
    monkeypatch.setenv("DELIVERY_RECEIPT_PATH", str(path))

    clear_delivery_receipt()

    assert not path.exists()
