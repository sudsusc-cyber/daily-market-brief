"""单元测试:重试装饰器(指数退避)。"""

from __future__ import annotations

import logging
import time

import pytest

from src.utils.retry import _redact_secrets, retry


class TestRetry:
    def test_success_first_attempt_no_delay(self) -> None:
        calls = {"n": 0}

        @retry(max_attempts=3, base_delay=10.0)
        def fn() -> str:
            calls["n"] += 1
            return "ok"

        t0 = time.time()
        assert fn() == "ok"
        assert calls["n"] == 1
        # 没失败,不应等待 base_delay 那么久
        assert time.time() - t0 < 1.0

    def test_recover_on_second_attempt(self) -> None:
        calls = {"n": 0}

        @retry(max_attempts=3, base_delay=0.05, jitter=0.0)
        def fn() -> str:
            calls["n"] += 1
            if calls["n"] < 2:
                raise RuntimeError("transient")
            return "ok"

        assert fn() == "ok"
        assert calls["n"] == 2

    def test_exhausted_raises_last(self) -> None:
        calls = {"n": 0}

        @retry(max_attempts=3, base_delay=0.05, jitter=0.0)
        def fn() -> str:
            calls["n"] += 1
            raise ValueError(f"attempt {calls['n']}")

        with pytest.raises(ValueError, match="attempt 3"):
            fn()
        assert calls["n"] == 3

    def test_only_catches_specified_exceptions(self) -> None:
        calls = {"n": 0}

        @retry(max_attempts=3, base_delay=0.05, exceptions=(ValueError,))
        def fn() -> str:
            calls["n"] += 1
            raise TypeError("nope")

        with pytest.raises(TypeError):
            fn()
        # TypeError 不在 catch 列表,应一次就抛
        assert calls["n"] == 1


class TestRedactSecrets:
    """异常日志安全:retry 失败时打印的异常 message 必须 redact 掉 query string 凭据。"""

    def test_redact_api_key(self) -> None:
        s = "404 for url: https://api.foo.com/x?api_key=abc123secret&series=Y"
        out = _redact_secrets(s)
        assert "abc123secret" not in out
        assert "api_key=***" in out

    def test_redact_token(self) -> None:
        assert _redact_secrets("...?token=BEARER_xyz&q=1") == "...?token=***&q=1"

    def test_redact_access_token(self) -> None:
        assert "ya29" not in _redact_secrets("oauth?access_token=ya29.abcdef")

    def test_redact_apikey_no_underscore(self) -> None:
        assert _redact_secrets("?apikey=plain123") == "?apikey=***"

    def test_redact_case_insensitive(self) -> None:
        assert _redact_secrets("?API_KEY=XXX") == "?API_KEY=***"

    def test_no_redact_normal_text(self) -> None:
        s = "Connection refused"
        assert _redact_secrets(s) == s

    def test_redact_empty(self) -> None:
        assert _redact_secrets("") == ""


class TestRetryLogRedaction:
    """retry 用尽时输出的 warning 必须经过 redact,不能含原始 secret。"""

    def test_exhausted_log_redacts_url_secret(self, caplog) -> None:
        @retry(max_attempts=2, base_delay=0.01, jitter=0.0)
        def fn() -> None:
            raise ConnectionError(
                "404 Client Error for url: https://api.example.com/?api_key=SECRET_TOKEN_42"
            )

        with caplog.at_level(logging.WARNING, logger="src.utils.retry"), \
             pytest.raises(ConnectionError):
            fn()
        # 关键断言:警告日志里不能含原始 secret
        log_text = "\n".join(r.getMessage() for r in caplog.records)
        assert "SECRET_TOKEN_42" not in log_text, (
            f"retry exhaustion log 泄露了 secret:\n{log_text}"
        )
        assert "api_key=***" in log_text  # 而是被 redact 成 ***
