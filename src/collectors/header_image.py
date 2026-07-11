"""每日刊头图选取 — 三层降级 + 本地缓存 + inline CID 嵌入。

优先级:
  1. Pexels 白名单 (config/curated_images.json) — 按日期序数确定性选取
  2. Bing 每日壁纸 API — 8s 超时
  3. 本地 fallback (assets/fallback_header.jpg) — 永远成功

返回值:
  {"url": "cid:header_image",
   "source": "pexels" | "bing" | "local",
   "id": str | None,
   "local_path": Path}

  调用方按 cid="header_image" 把 local_path 作为 InlineImage 附入 MIME。
  **统一走 inline 是因为 Android QQ 邮箱不会自动加载远程 <img src="https://...">**,
  iOS / 桌面正常但 Android 用户看不到刊头图。inline CID 任何客户端都能渲染,
  代价是邮件多 200-500 KB(可忽略)。

铁律:本函数绝对不抛异常。下载失败 → 降级到下一层。
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
_FALLBACK_IMAGE = _PROJECT_ROOT / "assets" / "fallback_header.jpg"
_CACHE_DIR = _PROJECT_ROOT / "state" / "header_cache"
_HEADER_CID = "header_image"
_TIMEOUT = 8
_MAX_IMAGE_BYTES = 8 * 1024 * 1024
_BING_CACHE_KEEP_DAYS = 30  # bing_<date>.jpg 每天新增,超过 N 天删除以防 actions/cache 膨胀

_SEASON_MAP = {
    1: "winter", 2: "winter",
    3: "spring", 4: "spring", 5: "spring",
    6: "summer", 7: "summer", 8: "summer",
    9: "autumn", 10: "autumn", 11: "autumn",
    12: "winter",
}


def _pick_season(today: date) -> str:
    return _SEASON_MAP[today.month]


# 常见图片格式 magic bytes(前几个字节):
#   JPEG: FF D8 FF
#   PNG:  89 50 4E 47 0D 0A 1A 0A
#   GIF:  47 49 46 38 (37|39) 61
#   WEBP: "RIFF" .... "WEBP"
def _is_image_bytes(data: bytes) -> bool:
    """magic bytes 嗅探:不依赖 PIL,几字节判断。
    防"Pexels/Bing 返回 200 HTML 拦截页 → 写成 .jpg → 当 image/jpeg 附件发"。
    """
    if len(data) < 12:
        return False
    if data[:3] == b"\xff\xd8\xff":  # JPEG
        return True
    if data[:8] == b"\x89PNG\r\n\x1a\n":  # PNG
        return True
    if data[:6] in (b"GIF87a", b"GIF89a"):  # GIF
        return True
    return data[:4] == b"RIFF" and data[8:12] == b"WEBP"  # WEBP


def _download(url: str, dest: Path) -> Path:
    """下载到 dest;已存在且非空 → 直接复用(免重复下载)。失败抛异常,
    由调用方捕获并降级到下一层。

    校验:
    - HTTP Content-Type 必须 image/* (HTML 拦截页通常是 text/html)
    - 字节 magic bytes 必须匹配 JPEG/PNG/GIF/WEBP 之一
    任一失败 → 抛异常,由调用方降级,绝不写盘。
    原子写:.tmp + replace,避免半下载文件残留被下次 cache 命中。
    """
    if dest.exists() and dest.stat().st_size > 0:
        size = dest.stat().st_size
        try:
            valid_cache = size <= _MAX_IMAGE_BYTES and _is_image_bytes(dest.read_bytes()[:32])
        except OSError:
            valid_cache = False
        if valid_cache:
            logger.info("header.cache.hit path=%s size=%d", dest.name, size)
            return dest
        logger.warning("header.cache.invalid path=%s size=%d; redownloading", dest.name, size)
        dest.unlink(missing_ok=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    # ProxyHandler({}) 强制 bypass 系统代理(避免 Surge / ClashX 接管导致超时)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 daily-market-brief/1.0"},
    )
    with opener.open(req, timeout=_TIMEOUT) as resp:
        content_type = (resp.headers.get("Content-Type") or "").lower()
        data = resp.read(_MAX_IMAGE_BYTES + 1)
    if not data:
        raise OSError("empty response")
    if len(data) > _MAX_IMAGE_BYTES:
        raise OSError(f"image exceeds {_MAX_IMAGE_BYTES} bytes")
    if not content_type.startswith("image/"):
        raise OSError(f"non-image Content-Type: {content_type!r} (HTML 拦截页?)")
    if not _is_image_bytes(data):
        raise OSError(
            f"non-image magic bytes: head={data[:12]!r}(声称 {content_type})"
        )
    # 原子写:tmp + replace,失败 / 进程被杀不会留半文件
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(dest)
    logger.info("header.cache.miss path=%s size=%d type=%s", dest.name, len(data), content_type)
    return dest


def _tier1_pexels(today: date) -> dict:
    library = json.loads(_CURATED_JSON.read_text())
    season = _pick_season(today)
    pool = library["seasons"][season]
    entry = pool[today.toordinal() % len(pool)]
    sid = entry["id"]
    url = library["url_template"].replace("{id}", sid)
    # 文件名含版式版本，避免沿用历史 1280×400 窄幅裁切缓存。
    local_path = _download(url, _CACHE_DIR / f"pexels_{sid}_1280x640.jpg")
    logger.info("header.pexels id=%s season=%s", sid, season)
    return {
        "url": f"cid:{_HEADER_CID}",
        "source": "pexels",
        "id": sid,
        "local_path": local_path,
    }


def _tier2_bing(today: date) -> dict:
    # today 必须是 BJT 当日(由 pick_header_image 注入)。GH Actions runner 以 UTC 跑,
    # date.today() 会在 BJT 06:00-08:00 间(UTC 22:00-00:00)给出"前一日 UTC",
    # 与缓存键 bing_<today>.jpg 错位,导致每次都重新下载。强制传参以杜绝这条隐患。
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    api = "https://www.bing.com/HPImageArchive.aspx?format=js&idx=0&n=1&mkt=zh-CN"
    with opener.open(api, timeout=_TIMEOUT) as resp:
        data = json.loads(resp.read().decode())
    raw_url = data["images"][0]["url"]
    url = f"https://www.bing.com{raw_url}" if raw_url.startswith("/") else raw_url
    # 强制宽高参数
    if "1280" not in url:
        url = url.split("&w=")[0] + "&w=1280&h=400&rs=1&c=4"
    local_path = _download(url, _CACHE_DIR / f"bing_{today.isoformat()}.jpg")
    # 日志只打 host + path,丢弃 query —— Bing 当前 query 无凭据,但截 query 是
    # 防御 future drift(若上游返回的 raw_url 偶发携带 token / session id 一类参数)
    log_url = url.split("?", 1)[0]
    logger.info("header.bing url=%s", log_url)
    return {
        "url": f"cid:{_HEADER_CID}",
        "source": "bing",
        "id": None,
        "local_path": local_path,
    }


def _tier3_local() -> dict:
    logger.warning("header.fallback source=local")
    return {
        "url": f"cid:{_HEADER_CID}",
        "source": "local",
        "id": None,
        "local_path": _FALLBACK_IMAGE,
    }


def _purge_old_bing_cache(today: date, keep_days: int = _BING_CACHE_KEEP_DAYS) -> None:
    """清理超过 keep_days 的 bing_<YYYY-MM-DD>.jpg。

    Pexels 文件不动(库 ID 有限,会复用)。失败仅 warning,不阻断主流程。
    """
    if not _CACHE_DIR.exists():
        return
    cutoff = today.toordinal() - keep_days
    for f in _CACHE_DIR.glob("bing_*.jpg"):
        try:
            date_str = f.stem.removeprefix("bing_")
            file_date = date.fromisoformat(date_str)
            if file_date.toordinal() < cutoff:
                f.unlink()
                logger.info("header.cache.purged path=%s", f.name)
        except (ValueError, OSError) as exc:
            logger.warning("header.cache.purge_failed path=%s exc=%s", f.name, exc)


def pick_header_image(today: date | None = None) -> dict:
    """三层降级选取刊头图,永不抛异常。返回 inline CID + 本地图片路径。"""
    if today is None:
        today = date.today()

    _purge_old_bing_cache(today)

    try:
        return _tier1_pexels(today)
    except Exception as exc:
        logger.warning("header.pexels.failed error=%r", exc)

    try:
        return _tier2_bing(today)
    except Exception as exc:
        logger.warning("header.bing.failed error=%r", exc)

    return _tier3_local()
