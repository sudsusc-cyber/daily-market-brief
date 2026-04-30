"""tests/test_header_image.py — M5 刊头图 collector 单元测试。"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.collectors.header_image import (
    _FALLBACK_CID,
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

def test_tier1_returns_pexels_url(tmp_path):
    library = {
        "url_template": "https://images.pexels.com/photos/{id}/img.jpg",
        "seasons": {
            "winter": [{"id": "111", "slug": "snow-field"}, {"id": "222", "slug": "ice-lake"}],
            "spring": [],
            "summer": [],
            "autumn": [],
        },
    }
    cfg = tmp_path / "curated_images.json"
    cfg.write_text(json.dumps(library))

    with patch("src.collectors.header_image._CURATED_JSON", cfg):
        result = _tier1_pexels(date(2025, 1, 1))

    assert result["source"] == "pexels"
    assert result["url"].startswith("https://images.pexels.com")
    assert result["id"] in ("111", "222")


def test_tier1_deterministic(tmp_path):
    pool = [{"id": str(i), "slug": f"img-{i}"} for i in range(50)]
    library = {
        "url_template": "https://images.pexels.com/photos/{id}/img.jpg",
        "seasons": {"spring": pool, "summer": pool, "autumn": pool, "winter": pool},
    }
    cfg = tmp_path / "curated_images.json"
    cfg.write_text(json.dumps(library))

    d = date(2025, 4, 10)
    with patch("src.collectors.header_image._CURATED_JSON", cfg):
        r1 = _tier1_pexels(d)
        r2 = _tier1_pexels(d)
    assert r1["id"] == r2["id"]

    d2 = date(2025, 4, 11)
    with patch("src.collectors.header_image._CURATED_JSON", cfg):
        r3 = _tier1_pexels(d2)
    # 连续两天必须不同(池足够大)
    assert r1["id"] != r3["id"]


# ─────────────────────────  tier 2  ──────────────────────────────────

def test_tier2_bing_success():
    fake_resp = MagicMock()
    fake_resp.__enter__ = lambda s: s
    fake_resp.__exit__ = MagicMock(return_value=False)
    fake_resp.read.return_value = json.dumps({
        "images": [{"url": "/th?id=OHR.Example&w=1920&h=1080"}]
    }).encode()

    with patch("src.collectors.header_image.urllib.request.build_opener") as mock_opener:
        opener_inst = MagicMock()
        mock_opener.return_value = opener_inst
        opener_inst.open.return_value = fake_resp
        result = _tier2_bing()

    assert result["source"] == "bing"
    assert result["url"].startswith("https://www.bing.com")
    assert "1280" in result["url"]


# ─────────────────────────  tier 3  ──────────────────────────────────

def test_tier3_returns_cid():
    result = _tier3_local()
    assert result["source"] == "local"
    assert result["url"] == f"cid:{_FALLBACK_CID}"
    assert result["id"] is None


# ─────────────────────────  pick_header_image  ───────────────────────

def test_pick_header_image_tier1_wins(tmp_path):
    pool = [{"id": "999", "slug": "snow"}]
    library = {
        "url_template": "https://images.pexels.com/photos/{id}/img.jpg",
        "seasons": {"winter": pool, "spring": pool, "summer": pool, "autumn": pool},
    }
    cfg = tmp_path / "curated_images.json"
    cfg.write_text(json.dumps(library))

    with patch("src.collectors.header_image._CURATED_JSON", cfg):
        result = pick_header_image(date(2025, 1, 5))

    assert result["source"] == "pexels"


def test_pick_header_image_falls_to_bing_when_tier1_fails():
    fake_resp = MagicMock()
    fake_resp.__enter__ = lambda s: s
    fake_resp.__exit__ = MagicMock(return_value=False)
    fake_resp.read.return_value = json.dumps({
        "images": [{"url": "/th?id=OHR.Fallback&w=1920&h=1080"}]
    }).encode()

    with patch("src.collectors.header_image._CURATED_JSON", Path("/nonexistent/missing.json")):
        with patch("src.collectors.header_image.urllib.request.build_opener") as mock_opener:
            opener_inst = MagicMock()
            mock_opener.return_value = opener_inst
            opener_inst.open.return_value = fake_resp
            result = pick_header_image(date(2025, 1, 5))

    assert result["source"] == "bing"


def test_pick_header_image_falls_to_local_when_all_fail():
    with patch("src.collectors.header_image._CURATED_JSON", Path("/nonexistent/missing.json")):
        with patch("src.collectors.header_image.urllib.request.build_opener") as mock_opener:
            opener_inst = MagicMock()
            mock_opener.return_value = opener_inst
            opener_inst.open.side_effect = OSError("network down")
            result = pick_header_image(date(2025, 1, 5))

    assert result["source"] == "local"
    assert result["url"] == f"cid:{_FALLBACK_CID}"


def test_pick_header_image_never_raises():
    with patch("src.collectors.header_image._CURATED_JSON", Path("/no/such/file.json")):
        with patch("src.collectors.header_image.urllib.request.build_opener") as mock_opener:
            opener_inst = MagicMock()
            mock_opener.return_value = opener_inst
            opener_inst.open.side_effect = RuntimeError("unexpected")
            result = pick_header_image(date(2025, 7, 4))

    assert "url" in result
    assert "source" in result
