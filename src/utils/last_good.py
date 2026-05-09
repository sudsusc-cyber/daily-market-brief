"""
通用 last-known-good 缓存。

用途
----
当外部数据源(yfinance / RSS / 第三方 API 等)在 GH Actions runner 上间歇失败时,
让 collector 沿用最近一次成功值,而不是直接显示"数据获取失败"。

设计
----
单文件 `state/last_good.json`,namespace.key 命名法。值结构:
  {
    "value":    <任意 JSON 可序列化对象>,
    "saved_at": "YYYY-MM-DD"
  }

原子写入(.tmp → os.replace),损坏时静默返回 None(防御式),不抛异常。

适用与不适用
-----------
适用:日频或更慢的指标(VIX / DXY / Shiller PE / 持仓周收盘 等),沿用 1-7 天
      可接受。

不适用:已经有专属持久化的模块(buffett_13f → state/last_13f.json,header
      → state/header_cache/),不要重复存。

最长沿用窗口
-----------
默认 7 天。超过 7 天的沿用值被视为已失效,collector 应当作真正失败处理
(显示"数据获取失败 · 已超过 7 天")。这个数字基于:
  - VIX/DXY 是日频,7 天差距已经显著
  - 数据源连续 7 天故障是真正的问题,不应该再静默掩盖
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_MAX_AGE_DAYS = 7


def _path(state_dir: Path) -> Path:
    return state_dir / "last_good.json"


class LastGoodCache:
    """单文件、namespace.key 命名的 last-known-good 缓存。

    用法:
        cache = LastGoodCache(Path("state"))
        # 拉取
        cached = cache.get("sentiment.VIX")
        if cached:
            value, saved_at = cached
        # 保存
        cache.put("sentiment.VIX", {"current": 17.5, "prior": 16.9}, today=date.today())
        # 判断是否过期
        if cache.is_stale(saved_at, today=date.today(), max_age_days=7):
            ...
    """

    def __init__(self, state_dir: Path):
        self._path = _path(state_dir)
        self._data: dict[str, dict[str, Any]] = {}
        self._loaded = False

    def _load(self) -> None:
        """惰性加载。文件不存在或损坏 → 视为空缓存,不抛。"""
        if self._loaded:
            return
        self._loaded = True
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self._data = {
                    k: v for k, v in raw.items()
                    if isinstance(v, dict) and "value" in v and "saved_at" in v
                }
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("last_good.corrupted path=%s exc=%r", self._path, exc)
            self._data = {}

    def get(self, key: str) -> tuple[Any, str] | None:
        """返回 (value, saved_at_iso_str) 或 None。"""
        self._load()
        entry = self._data.get(key)
        if not entry:
            return None
        return entry["value"], entry["saved_at"]

    def put(self, key: str, value: Any, *, today: date | None = None) -> None:
        """保存 value 到 key,saved_at = today(默认今日)。

        立即原子写入磁盘(.tmp + os.replace),保证多 collector 并发或中途
        crash 不留半截文件。
        """
        self._load()
        if today is None:
            today = date.today()
        self._data[key] = {
            "value": value,
            "saved_at": today.isoformat(),
        }
        self._flush()

    def _flush(self) -> None:
        """原子写入。state_dir 不存在时记 warning 不抛。"""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            os.replace(tmp, self._path)
        except OSError as exc:
            logger.warning("last_good.write_failed path=%s exc=%r", self._path, exc)

    @staticmethod
    def is_stale(
        saved_at: str,
        *,
        today: date | None = None,
        max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    ) -> bool:
        """saved_at 早于 today - max_age_days 视为过期。

        saved_at 不可解析(损坏数据)时也视为过期(保守,让上层降级到错误显示)。
        """
        if today is None:
            today = date.today()
        try:
            d = date.fromisoformat(saved_at)
        except (ValueError, TypeError):
            return True
        return (today - d).days > max_age_days
