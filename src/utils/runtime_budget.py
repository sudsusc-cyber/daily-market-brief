"""Bound collection and LLM wall time without counting elapsed time as API usage."""

import logging
import math
import os
import signal
import threading
import time
from collections.abc import Callable
from typing import TypeVar

logger = logging.getLogger(__name__)
T = TypeVar("T")


class StageTimeout(BaseException):
    """Must pass through collectors' broad except Exception/retry handlers."""


class RuntimeBudget:
    """POSIX main-process stage watchdog; no abandoned worker can keep fetching.

    Production runs on Linux; local macOS previews use the same behavior.
    The shared workflow cutoff includes setup and leaves the SMTP reserve intact.
    """

    def __init__(self, seconds: float = 900.0) -> None:
        self.deadline = time.monotonic() + min(seconds, llm_wall_timeout_seconds())

    def call(self, fn: Callable[..., T], *args, seconds: float,
             fallback: Callable[[], T], **kwargs) -> T:
        return self.run(getattr(fn, "__name__", "stage"), lambda: fn(*args, **kwargs),
                        seconds=seconds, fallback=fallback)

    def run(self, name: str, fn: Callable[[], T], *, seconds: float,
            fallback: Callable[[], T]) -> T:
        remaining = min(seconds, self.deadline - time.monotonic())
        if remaining <= 0:
            logger.warning("runtime.stage_skipped name=%s budget_exhausted=true", name)
            return fallback()
        if threading.current_thread() is not threading.main_thread() or not hasattr(signal, "setitimer"):
            # Never silently execute an unbounded operation on unsupported hosts.
            logger.warning("runtime.watchdog_unavailable name=%s", name)
            return fallback()

        def expired(_signum, _frame):
            raise StageTimeout(name)

        previous_handler = signal.getsignal(signal.SIGALRM)
        previous_timer = signal.getitimer(signal.ITIMER_REAL)
        started = time.monotonic()
        limit = min(remaining, previous_timer[0]) if previous_timer[0] > 0 else remaining
        signal.signal(signal.SIGALRM, expired)
        signal.setitimer(signal.ITIMER_REAL, max(0.001, limit))
        timed_out = False
        try:
            return fn()
        except StageTimeout:
            timed_out = True
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous_handler)
            if previous_timer[0] > 0:
                signal.setitimer(signal.ITIMER_REAL,
                                 max(0.001, previous_timer[0] - (time.monotonic() - started)),
                                 previous_timer[1])
        if timed_out:
            logger.warning("runtime.stage_timeout name=%s limit_seconds=%.2f", name, limit)
        return fallback()


def llm_wall_timeout_seconds() -> float:
    """At most 15 minutes from process start, and before the CI send reserve.

    The workflow records its cutoff before dependency installation. A malformed
    explicit cutoff fails closed for LLM calls, preserving deterministic fallbacks.
    Local runs without the workflow variable retain the 15-minute process cap.
    This limits LLM work; it cannot preempt unrelated hung collectors or SMTP.
    """
    cutoff = os.environ.get("BRIEF_LLM_CUTOFF_EPOCH")
    if cutoff is None:
        return 900.0
    try:
        remaining = float(cutoff) - time.time()
        if not math.isfinite(remaining):
            raise ValueError("non-finite cutoff")
    except (ValueError, OverflowError):
        logger.warning("runtime.invalid_llm_cutoff disabling_llm=true")
        return 0.0
    return max(0.0, min(900.0, remaining))
