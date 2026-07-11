from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.utils.fetch_rss import fetch_rss


def _response(body: bytes, content_type: str) -> MagicMock:
    response = MagicMock()
    response.headers = {"Content-Type": content_type}
    response.iter_content.return_value = [body]
    return response


def test_html_200_response_is_rejected(monkeypatch) -> None:
    response = _response(b"<html><body>Access denied</body></html>", "text/html")
    monkeypatch.setattr("src.utils.fetch_rss.requests.get", lambda *a, **k: response)

    with pytest.raises(ValueError, match="HTML"):
        fetch_rss("https://example.com/feed")
    response.close.assert_called_once_with()


def test_unrecognized_non_html_body_is_rejected(monkeypatch) -> None:
    response = _response(b"not a feed", "text/plain")
    monkeypatch.setattr("src.utils.fetch_rss.requests.get", lambda *a, **k: response)

    with pytest.raises(ValueError, match="recognizable"):
        fetch_rss("https://example.com/feed")


def test_valid_empty_rss_is_accepted(monkeypatch) -> None:
    body = b'<?xml version="1.0"?><rss version="2.0"><channel><title>x</title></channel></rss>'
    response = _response(body, "application/rss+xml")
    monkeypatch.setattr("src.utils.fetch_rss.requests.get", lambda *a, **k: response)

    feed = fetch_rss("https://example.com/feed")

    assert feed.version == "rss20"
    assert feed.entries == []
    response.close.assert_called_once_with()
