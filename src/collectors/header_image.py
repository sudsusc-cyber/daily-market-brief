"""每日刊头图选取 — 三层降级。

优先级:
  1. Pexels 白名单 (config/curated_images.json) — 按日期序数确定性选取,无网络依赖
  2. Bing 每日壁纸 API — 5s 超时,失败静默
  3. 本地 fallback (assets/fallback_header.jpg) — 永远成功,返回 CID

返回值:
  {"url": str, "source": "pexels" | "bing" | "local", "id": str | None}

  source=="local" 时 url=="cid:header_fallback",调用方负责将
  assets/fallback_header.jpg 作为 CID "header_fallback" 附入 MIME。

铁律:本函数绝对不抛异常。
"""

from __future__ import annotations

import json
import logging
import urllib.request
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_CURATED_JSON = _PROJECT_ROOT / "config" / "curated_images.json"
_FALLBACK_CID = "header_fallback"
_TIMEOUT = 5

_SEASON_MAP = {
    1: "winter", 2: "winter",
    3: "spring", 4: "spring", 5: "spring",
    6: "summer", 7: "summer", 8: "summer",
    9: "autumn", 10: "autumn", 11: "autumn",
    12: "winter",
}


def _pick_season(today: date) -> str:
    return _SEASON_MAP[today.month]


def _tier1_pexels(today: date) -> dict:
    library = json.loads(_CURATED_JSON.read_text())
    season = _pick_season(today)
    pool = library["seasons"][season]
    entry = pool[today.toordinal() % len(pool)]
    sid = entry["id"]
    url = library["url_template"].replace("{id}", sid)
    logger.info("header.pexels id=%s season=%s", sid, season)
    return {"url": url, "source": "pexels", "id": sid}


def _tier2_bing() -> dict:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    api = "https://www.bing.com/HPImageArchive.aspx?format=js&idx=0&n=1&mkt=zh-CN"
    with opener.open(api, timeout=_TIMEOUT) as resp:
        data = json.loads(resp.read().decode())
    raw_url = data["images"][0]["url"]
    url = f"https://www.bing.com{raw_url}" if raw_url.startswith("/") else raw_url
    # 强制宽高参数
    if "1280" not in url:
        url = url.split("&w=")[0] + "&w=1280&h=400&rs=1&c=4"
    logger.info("header.bing url=%s", url)
    return {"url": url, "source": "bing", "id": None}


def _tier3_local() -> dict:
    logger.warning("header.fallback source=local")
    return {"url": f"cid:{_FALLBACK_CID}", "source": "local", "id": None}


def pick_header_image(today: date | None = None) -> dict:
    """三层降级选取刊头图,永不抛异常。"""
    if today is None:
        today = date.today()

    try:
        return _tier1_pexels(today)
    except Exception as exc:
        logger.warning("header.pexels.failed error=%r", exc)

    try:
        return _tier2_bing()
    except Exception as exc:
        logger.warning("header.bing.failed error=%r", exc)

    return _tier3_local()
