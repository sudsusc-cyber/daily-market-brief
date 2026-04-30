"""
美股节假日判断(M6)。

逻辑:
- 北京时间今天发邮件反映的是"美股最近一个交易日"的数据(通常是北京前一天对应的美东交易日)。
- 若北京"今天"对应的美股目标日期(=今天日历日 - 1 天)是周末或 NYSE 节假日,则跳过发送。
- 节假日表存于 state/us_holidays_<year>.json,每年 11 月底人工刷新次年表。

主入口:should_send_today(today_bj: date) -> tuple[bool, str]
  - 返回 (True, 原因) 或 (False, 原因)
  - main.py 启动时调用,False 时直接 sys.exit(0)
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_STATE_DIR = _PROJECT_ROOT / "state"


def _load_holidays(year: int) -> set[date]:
    """读 state/us_holidays_<year>.json;缺文件时返回空集合(不阻塞发送)。"""
    path = _STATE_DIR / f"us_holidays_{year}.json"
    if not path.exists():
        logger.warning("holidays.file_missing year=%d path=%s", year, path)
        return set()
    try:
        data = json.loads(path.read_text())
        out: set[date] = set()
        for h in data.get("holidays", []):
            out.add(date.fromisoformat(h["date"]))
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("holidays.parse_failed year=%d error=%r", year, exc)
        return set()


def is_us_market_open(d: date) -> bool:
    """美股该日是否开盘:工作日 + 非 NYSE 节假日。"""
    if d.weekday() >= 5:  # 周六(5) / 周日(6)
        return False
    return d not in _load_holidays(d.year)


def should_send_today(today_bj: date) -> tuple[bool, str]:
    """
    今天(北京)是否应该发邮件。

    规则:邮件反映"美股最近一个交易日"的数据,目标日期 = today_bj - 1 day。
    若该目标日期是美股休市日(周末/节假日)→ 跳过发送。
    """
    target = today_bj - timedelta(days=1)
    if not is_us_market_open(target):
        return False, f"美股 {target.isoformat()} 休市(周末/节假日),今日跳过"
    return True, f"美股 {target.isoformat()} 正常交易,继续发送"
