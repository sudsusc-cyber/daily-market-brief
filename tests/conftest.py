"""Ordinary regression tests cannot spend API credits or send real email."""

import socket

import pytest
from curl_cffi import Curl


@pytest.fixture(autouse=True)
def block_unmocked_network(monkeypatch, request):
    if request.node.get_closest_marker("dryrun"):
        return

    def blocked(*args, **kwargs):
        pytest.fail("Unmocked network connection in offline test; use an explicit dryrun marker")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    # yfinance's native libcurl bypasses Python socket.connect entirely.
    monkeypatch.setattr(Curl, "perform", blocked)
