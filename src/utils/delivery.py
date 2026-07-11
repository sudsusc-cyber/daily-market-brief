"""GitHub Actions 邮件送达凭证。

SMTP 成功返回后才写入一次性文件。daily.yml 通过独立步骤检查该文件，监控再从
Jobs API 读取该步骤结论，从而区分“工作流成功退出”和“邮件确实完成 SMTP 发送”。
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

_RECEIPT_ENV = "DELIVERY_RECEIPT_PATH"


def clear_delivery_receipt() -> None:
    """运行开始时删除旧凭证；未配置路径时不做任何事。"""
    raw_path = os.environ.get(_RECEIPT_ENV, "").strip()
    if not raw_path:
        return
    Path(raw_path).unlink(missing_ok=True)


def write_delivery_receipt(*, sent_at: datetime, run_id: str | None = None) -> None:
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
                "sent_at": sent_at.isoformat(timespec="seconds"),
                "run_id": run_id or "",
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    os.replace(tmp, path)
