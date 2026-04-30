"""单元测试:重试装饰器(指数退避)。"""

from __future__ import annotations

import time

import pytest

from src.utils.retry import retry


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
