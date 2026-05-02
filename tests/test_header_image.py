"""tests/test_header_image.py — M5 刊头图 collector 单元测试。

新行为(v2):三层都返回 inline CID + 本地图片路径,Pexels/Bing 也会下载到本地。
解决 Android QQ 邮箱不加载远程图的问题。
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.collectors.header_image import (
    _HEADER_CID,
    _FALLBACK_IMAGE,
    _pick_season,
    _tier1_pexels,
    _tier2_bing,
    _tier3_local,
    pick_header_image,
)


# ─────────────────────────  season mapping  ──────────────────────────

@pytest.mark.parametrize("month,expected", [
    (1, "winter"), (2, "winter"), (12, "winter"),
    (3, "spring"), (4, "spring"), (5, "spring"),
    (6, "summer"), (7, "summer"), (8, "summer"),
    (9, "autumn"), (10, "autumn"), (11, "autumn"),
])
def test_pick_season(month, expected):
    assert _pick_season(date(2025, month, 15)) == expected


# ─────────────────────────  tier 1  ──────────────────────────────────

def _write_library(tmp_path: Path) -> Path:
    """构造一个最小的 curated_images.json,返回路径"""
    library = {
        "url_template": "https://images.pexels.com/photos/{id}/img.jpg",
        "seasons": {
            "winter": [{"id": "111", "slug": "snow-field"}, {"id": "222", "slug": "ice-lake"}],
            "spring": [{"id": "333", "slug": "blossom"}],
            "summer": [{"id": "444", "slug": "beach"}],
            "autumn": [{"id": "555", "slug": "leaves"}],
        },
    }
    cfg = tmp_path / "curated_images.json"
    cfg.write_text(json.dumps(library))
    return cfg


def test_tier1_returns_cid_and_local_path(tmp_path):
    cfg = _write_library(tmp_path)
    fake_path = tmp_path / "fake_pexels.jpg"
    fake_path.write_bytes(b"fake-jpeg-bytes")

    with patch("src.collectors.header_image._CURATED_JSON", cfg), \
         patch("src.collectors.header_image._download", return_value=fake_path):
        result = _tier1_pexels(date(2025, 1, 1))

    assert result["source"] == "pexels"
    assert result["url"] == f"cid:{_HEADER_CID}"
    assert result["id"] in ("111", "222")
    assert result["local_path"] == fake_path


def test_tier1_deterministic(tmp_path):
    pool = [{"id": str(i), "slug": f"img-{i}"} for i in range(50)]
    library = {
        "url_template": "https://images.pexels.com/photos/{id}/img.jpg",
        "seasons": {"spring": pool, "summer": pool, "autumn": pool, "winter": pool},
    }
    cfg = tmp_path / "curated_images.json"
    cfg.write_text(json.dumps(library))
    fake_path = tmp_path / "fake.jpg"
    fake_path.write_bytes(b"x")

    with patch("src.collectors.header_image._CURATED_JSON", cfg), \
         patch("src.collectors.header_image._download", return_value=fake_path):
        d = date(2025, 4, 10)
        r1 = _tier1_pexels(d)
        r2 = _tier1_pexels(d)
        assert r1["id"] == r2["id"]

        d2 = date(2025, 4, 11)
        r3 = _tier1_pexels(d2)
    # 连续两天必须不同(池足够大)
    assert r1["id"] != r3["id"]


def test_tier1_propagates_download_failure(tmp_path):
    """下载失败必须把异常抛出来,让 pick_header_image 降级到 tier 2"""
    cfg = _write_library(tmp_path)

    with patch("src.collectors.header_image._CURATED_JSON", cfg), \
         patch("src.collectors.header_image._download", side_effect=OSError("network down")):
        with pytest.raises(OSError):
            _tier1_pexels(date(2025, 1, 1))


# ─────────────────────────  tier 2  ──────────────────────────────────

def test_tier2_bing_success(tmp_path):
    fake_resp = MagicMock()
    fake_resp.__enter__ = lambda s: s
    fake_resp.__exit__ = MagicMock(return_value=False)
    fake_resp.read.return_value = json.dumps({
        "images": [{"url": "/th?id=OHR.Example&w=1920&h=1080"}]
    }).encode()
    fake_path = tmp_path / "fake_bing.jpg"
    fake_path.write_bytes(b"fake-bing-bytes")

    with patch("src.collectors.header_image.urllib.request.build_opener") as mock_opener, \
         patch("src.collectors.header_image._download", return_value=fake_path):
        opener_inst = MagicMock()
        mock_opener.return_value = opener_inst
        opener_inst.open.return_value = fake_resp
        result = _tier2_bing(date(2025, 1, 5))

    assert result["source"] == "bing"
    assert result["url"] == f"cid:{_HEADER_CID}"
    assert result["local_path"] == fake_path


def test_tier2_propagates_download_failure():
    """API 拿到 URL 但下载失败 → 抛异常,让 pick_header_image 降级到 tier 3"""
    fake_resp = MagicMock()
    fake_resp.__enter__ = lambda s: s
    fake_resp.__exit__ = MagicMock(return_value=False)
    fake_resp.read.return_value = json.dumps({
        "images": [{"url": "/th?id=OHR.Example"}]
    }).encode()

    with patch("src.collectors.header_image.urllib.request.build_opener") as mock_opener, \
         patch("src.collectors.header_image._download", side_effect=OSError("403")):
        opener_inst = MagicMock()
        mock_opener.return_value = opener_inst
        opener_inst.open.return_value = fake_resp
        with pytest.raises(OSError):
            _tier2_bing(date(2025, 1, 5))


# ─────────────────────────  tier 3  ──────────────────────────────────

def test_tier3_returns_fallback_path():
    result = _tier3_local()
    assert result["source"] == "local"
    assert result["url"] == f"cid:{_HEADER_CID}"
    assert result["id"] is None
    assert result["local_path"] == _FALLBACK_IMAGE


def test_tier3_fallback_file_exists():
    """assets/fallback_header.jpg 必须存在,否则 SMTP 附件会失败"""
    assert _FALLBACK_IMAGE.exists(), f"{_FALLBACK_IMAGE} 缺失"
    assert _FALLBACK_IMAGE.stat().st_size > 0


# ─────────────────────────  pick_header_image  ───────────────────────

def test_pick_header_image_tier1_wins(tmp_path):
    cfg = _write_library(tmp_path)
    fake_path = tmp_path / "fake.jpg"
    fake_path.write_bytes(b"x")

    with patch("src.collectors.header_image._CURATED_JSON", cfg), \
         patch("src.collectors.header_image._download", return_value=fake_path):
        result = pick_header_image(date(2025, 1, 5))

    assert result["source"] == "pexels"
    assert result["url"] == f"cid:{_HEADER_CID}"
    assert result["local_path"] == fake_path


def test_pick_header_image_falls_to_bing_when_tier1_fails(tmp_path):
    """Pexels 下载失败 → Bing 接管"""
    cfg = _write_library(tmp_path)
    fake_resp = MagicMock()
    fake_resp.__enter__ = lambda s: s
    fake_resp.__exit__ = MagicMock(return_value=False)
    fake_resp.read.return_value = json.dumps({
        "images": [{"url": "/th?id=OHR.Fallback"}]
    }).encode()
    fake_bing_path = tmp_path / "bing.jpg"
    fake_bing_path.write_bytes(b"bing-bytes")

    # _download 的两次调用:第一次(Pexels)失败,第二次(Bing)成功
    download_calls = []

    def fake_download(url, dest):
        download_calls.append(url)
        if "pexels" in url:
            raise OSError("pexels unreachable")
        return fake_bing_path

    with patch("src.collectors.header_image._CURATED_JSON", cfg), \
         patch("src.collectors.header_image.urllib.request.build_opener") as mock_opener, \
         patch("src.collectors.header_image._download", side_effect=fake_download):
        opener_inst = MagicMock()
        mock_opener.return_value = opener_inst
        opener_inst.open.return_value = fake_resp
        result = pick_header_image(date(2025, 1, 5))

    assert result["source"] == "bing"
    assert result["url"] == f"cid:{_HEADER_CID}"
    assert result["local_path"] == fake_bing_path


def test_pick_header_image_falls_to_local_when_all_fail():
    """Pexels + Bing 都挂 → 用本地 fallback,永不抛"""
    with patch("src.collectors.header_image._CURATED_JSON", Path("/nonexistent/missing.json")), \
         patch("src.collectors.header_image.urllib.request.build_opener") as mock_opener:
        opener_inst = MagicMock()
        mock_opener.return_value = opener_inst
        opener_inst.open.side_effect = OSError("network down")
        result = pick_header_image(date(2025, 1, 5))

    assert result["source"] == "local"
    assert result["url"] == f"cid:{_HEADER_CID}"
    assert result["local_path"] == _FALLBACK_IMAGE


def test_pick_header_image_never_raises():
    with patch("src.collectors.header_image._CURATED_JSON", Path("/no/such/file.json")), \
         patch("src.collectors.header_image.urllib.request.build_opener") as mock_opener:
        opener_inst = MagicMock()
        mock_opener.return_value = opener_inst
        opener_inst.open.side_effect = RuntimeError("unexpected")
        result = pick_header_image(date(2025, 7, 4))

    assert "url" in result
    assert "source" in result
    assert "local_path" in result


def test_returned_url_is_always_cid():
    """新行为铁律:三层 source 都必须返回 cid:header_image,从来不返回 https://"""
    with patch("src.collectors.header_image._CURATED_JSON", Path("/no/such/file.json")), \
         patch("src.collectors.header_image.urllib.request.build_opener") as mock_opener:
        opener_inst = MagicMock()
        mock_opener.return_value = opener_inst
        opener_inst.open.side_effect = OSError("down")
        result = pick_header_image(date(2025, 7, 4))

    assert result["url"].startswith("cid:")
    assert not result["url"].startswith("http")


# ─────────────────────────  _download cache  ─────────────────────────

def test_download_reuses_existing_cache(tmp_path):
    """已下载过的图不重复下载"""
    from src.collectors.header_image import _download

    cached = tmp_path / "cached.jpg"
    cached.write_bytes(b"already-here")

    # 不需要 mock urllib;若发生网络访问说明缓存命中失败
    with patch("src.collectors.header_image.urllib.request.build_opener") as mock_opener:
        result = _download("https://example.com/img.jpg", cached)
        assert mock_opener.call_count == 0  # 没访问网络

    assert result == cached
    assert result.read_bytes() == b"already-here"


def test_download_writes_to_disk(tmp_path):
    """缓存未命中时下载并写盘"""
    from src.collectors.header_image import _download

    fake_resp = MagicMock()
    fake_resp.__enter__ = lambda s: s
    fake_resp.__exit__ = MagicMock(return_value=False)
    fake_resp.read.return_value = b"new-image-bytes"

    dest = tmp_path / "subdir" / "new.jpg"  # 子目录会被自动创建

    with patch("src.collectors.header_image.urllib.request.build_opener") as mock_opener:
        opener_inst = MagicMock()
        mock_opener.return_value = opener_inst
        opener_inst.open.return_value = fake_resp
        result = _download("https://example.com/img.jpg", dest)

    assert result == dest
    assert dest.read_bytes() == b"new-image-bytes"


def test_download_empty_response_raises(tmp_path):
    """空响应不能写盘缓存(否则下次会"命中"空文件)"""
    from src.collectors.header_image import _download

    fake_resp = MagicMock()
    fake_resp.__enter__ = lambda s: s
    fake_resp.__exit__ = MagicMock(return_value=False)
    fake_resp.read.return_value = b""

    dest = tmp_path / "empty.jpg"

    with patch("src.collectors.header_image.urllib.request.build_opener") as mock_opener:
        opener_inst = MagicMock()
        mock_opener.return_value = opener_inst
        opener_inst.open.return_value = fake_resp
        with pytest.raises(OSError):
            _download("https://example.com/img.jpg", dest)

    assert not dest.exists()
