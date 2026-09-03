"""Bound production LLM work without charging collection time as API usage."""

import logging
import math
import os
import time

logger = logging.getLogger(__name__)


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
