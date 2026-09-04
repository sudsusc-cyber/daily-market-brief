"""GitHub Actions 邮件送达凭证。

SMTP 成功返回后才写入一次性文件。daily.yml 通过独立步骤检查该文件，监控再从
Jobs API 读取该步骤结论，从而区分“工作流成功退出”和“邮件确实完成 SMTP 发送”。
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

_RECEIPT_ENV = "DELIVERY_RECEIPT_PATH"


def clear_delivery_receipt() -> None:
    """运行开始时删除旧凭证；未配置路径时不做任何事。"""
    raw_path = os.environ.get(_RECEIPT_ENV, "").strip()
    if not raw_path:
        return
    Path(raw_path).unlink(missing_ok=True)


def write_delivery_receipt(
    *,
    sent_at: datetime,
    accepted_count: int,
    refused_count: int,
    run_id: str | None = None,
) -> None:
    """原子写入不含收件人或密钥的最小送达凭证。"""
    raw_path = os.environ.get(_RECEIPT_ENV, "").strip()
    if not raw_path:
        return

    path = Path(raw_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(
            {
                "accepted_count": max(0, int(accepted_count)),
                "refused_count": max(0, int(refused_count)),
                "sent_at": sent_at.isoformat(timespec="seconds"),
                "status": "full" if refused_count == 0 else "partial",
                "run_id": run_id or "",
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    os.replace(tmp, path)


def read_delivery_receipt(path: str | Path) -> dict:
    """读取并校验最小 receipt schema；无效内容直接视为失败。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("delivery receipt root must be an object")
    if data.get("status") not in {"full", "partial"}:
        raise ValueError("delivery receipt status is invalid")
    accepted = data.get("accepted_count")
    refused = data.get("refused_count")
    if type(accepted) is not int or accepted < 1:
        raise ValueError("delivery receipt has no accepted recipients")
    if type(refused) is not int or refused < 0:
        raise ValueError("delivery receipt refused_count is invalid")
    if (data["status"] == "full") != (refused == 0):
        raise ValueError("delivery receipt status/count mismatch")
    return data


def receipt_satisfies(path: str | Path, requirement: str) -> bool:
    data = read_delivery_receipt(path)
    if requirement == "accepted":
        return data["accepted_count"] > 0
    if requirement == "full":
        return data["status"] == "full" and data["refused_count"] == 0
    raise ValueError(f"unknown delivery requirement: {requirement}")


def _main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] not in {"accepted", "full"}:
        print("usage: python -m src.utils.delivery <accepted|full> <receipt-path>")
        return 2
    try:
        ok = receipt_satisfies(argv[2], argv[1])
    except Exception as exc:  # noqa: BLE001 - CLI 必须以非零表达任何无效 receipt
        print(f"delivery receipt invalid: {type(exc).__name__}: {exc}")
        return 1
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
