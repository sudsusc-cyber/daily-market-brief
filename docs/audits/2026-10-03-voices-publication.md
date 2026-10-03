# Voices translation recovery

User clarification: fix recurring translation failures and preserve useful voice coverage; do not solve the issue by hiding a failed section. The experimental rendering changes were reverted. Normal failure visibility, manifests, quality state and alerts remain unchanged.

The original recovery loop made only one request for selected evidence even with a second shared recovery call available. It now retries unresolved selected sources within that existing section budget. Successful siblings are not translated again. The section still has at most two recovery calls and six recovery items, subject to the global LLM deadline and token budget. Per-attempt diagnostics remain in addition to the first rejection and before/after record.

Translation repair prompts now include the failed candidate and actionable constraints about polarity, modality, actor/object relationships, quantity units and economic roles. The original source remains authoritative; every repaired translation passes the same source verifier. This is not an LLM self-approval step and does not lower editorial scores or factual safeguards.

Tests cover second-attempt recovery of a selected voice, preserving original evidence and first failure; selective retry of failed siblings; bounded timeout exhaustion; actionable repair input; and rejection of unsupported facts. The previous PR's positive/negative semantic fixtures remain in the full regression suite. This improves recoverability, but cannot promise universal correctness or guarantee a meaningful new speech every day. No real mail was sent.

Validation result: 2442 passed, 1 deselected under the existing pytest configuration. Repository Ruff and git diff whitespace checks passed.
