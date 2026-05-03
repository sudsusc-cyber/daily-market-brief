"""
通用重试装饰器(指数退避)。

用法:
    @retry(max_attempts=3, base_delay=1.0)
    def fetch_xxx(...):
        ...

由 PLAN 第 8 节"工程规范"指定:max_attempts=3 / 指数退避。

异常日志安全:retry 失败的异常 message 经 src.utils.secrets.redact_secrets
过滤掉 query-string 凭据,避免 collector 在 URL 里拼了 secret 时
通过日志泄露到 GH Actions 公开界面。
"""

from __future__ import annotations

import functools
import logging
import random
import time
from collections.abc import Callable
from typing import ParamSpec, TypeVar

from src.utils.secrets import redact_secrets

logger = logging.getLogger(__name__)

P = ParamSpec("P")
R = TypeVar("R")


# 兼容别名:test_retry.py 之前 import 了 _redact_secrets;不破坏外部测试。
_redact_secrets = redact_secrets


def retry(
    *,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    backoff: float = 2.0,
    jitter: float = 0.25,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """
    指数退避重试装饰器。

    - 第 i 次失败后等待 `base_delay * (backoff ** i) * (1 + random([-jitter, +jitter]))` 秒
    - 默认最多 3 次尝试,各自间隔 ~1s / ~2s
    - 超过 max_attempts 仍失败则抛出最后一次异常
    """

    def decorator(fn: Callable[P, R]) -> Callable[P, R]:
        @functools.wraps(fn)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            last_exc: BaseException | None = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return fn(*args, **kwargs)
                except exceptions as exc:
                    last_exc = exc
                    if attempt == max_attempts:
                        logger.warning(
                            "retry.exhausted fn=%s attempts=%d exc_type=%s msg=%s",
                            fn.__name__, attempt, type(exc).__name__,
                            redact_secrets(str(exc))[:300],
                        )
                        raise
                    delay = base_delay * (backoff ** (attempt - 1))
                    delay *= 1 + random.uniform(-jitter, jitter)
                    delay = max(0.05, delay)
                    logger.info(
                        "retry.sleep fn=%s attempt=%d/%d delay=%.2fs exc=%s",
                        fn.__name__, attempt, max_attempts, delay, type(exc).__name__,
                    )
                    time.sleep(delay)
            # 不可达
            assert last_exc is not None
            raise last_exc

        return wrapper

    return decorator
