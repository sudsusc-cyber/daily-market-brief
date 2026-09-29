# 2026-09-29 noon email semantic audit fixes

Audited the stored HTML, plain text and source mapping for formal run `36519910922` (base `f720c30`). SMTP acceptance is separate from content correctness. This change does not send another email.

## Confirmed defects and changes

1. The introduction incorrectly described both small-buy holdings as belonging to different markets and inferred that every other price was above its reference line. Generate exact signal counts deterministically, keep pending states separate, and make no unsupported price, geography or first-trigger claim.
2. A multi-company buyback roundup placed HSBC's amount under Tencent. Require a complete company-scoped sentence in both candidate selection and final source validation; a genuine independent Tencent sentence remains eligible.
3. Frontier copy retained promotional headlines and a nested publisher suffix. Reject promotional prose, retain independently supported factual sentences, and remove only recognized trailing publisher labels. Preserve prospective listing language.
4. Country membership incorrectly grouped economic stimulus with AI travel restrictions. Classify the actual event theme before the country fallback; preserve the existing three-paragraph limit and source links.
5. Existing bounded long-term watchpoints missed an explicitly named GPT release cancellation and a data center already under construction. Recognize those forms while retaining original/body evidence checks and negation guards.
6. Plain-text source labels ran together and lost destination URLs. Separate footer links onto lines and retain safe URLs; HTML citation styling is unchanged.
7. Common geographic/person names inconsistently remained in English. Apply a bounded presentation-only mapping without modifying original news fields, numbers, dates or arbitrary brand names.

## Verification

- Python 3.11 and 3.12: **1,814 tests passed** in each full suite.
- Ruff and `git diff --check`: passed.
- Added an allowlisted source-mapping fixture from the audited edition and regressions for both rejected copy and eligible factual alternatives. No recipient addresses or credentials are included.
- Offline replay of the stored edition: **90,695 HTML bytes**, below the 98,304-byte warning threshold. The replay preserves the archived holdings/data and uses the corrected publication paths; source excerpts, displayed copy and URL mappings passed the archive checker.
- Browser review checked the introduction, company/frontier copy, macro grouping, citation spacing and bounded judgment display. Plain-text source links were verified separately.

## Limits

The replay makes no fresh data requests, does not send SMTP, and does not replay the historical judgment frequency ledger. Two eligible bounded judgments in this fixture demonstrate corrected recognition, not a guarantee that every future edition will contain a judgment. Topic ranking and alias/presentation rules remain bounded heuristics. Source binding validates the supplied news text; it is not independent verification of every underlying external report. No valuation model, buying strategy, recipient, schedule or freshness policy changed.
