# October 6 editorial coverage remediation

## Observed production evidence

Run `37385964500`, attempt 1, edition `2026-10-06`, ran commit
`0e21317c6bf860e5858eab1c1ff5d64b0c35ae01`. The archived HTML/text hashes match
the delivered artifact. SMTP recorded full acceptance, accepted=4/refused=0.
HTML was 91,844 bytes. All 15 prices were current verified observations;
valuation included 10 carried references, not 15 newly updated estimates.

Voices collected 76 candidates. Five selected entries scored 4, four being
alternative reports of the same Altman statement. All five were rejected for
`speaker_date_unverified`, with `unsupported_publisher` diagnostics, including
NBC News. This establishes a parser/coverage bottleneck, not proof that every
candidate was actually recent speech. Unrelated speakers in search buckets were
correctly rejected and must not be attributed to the configured person.

## Changes

- Rank voices by actual named attribution, date availability and supported
  source before truncating the selection/translation window. Literal duplicate
  headlines keep alternative sources in reserve rather than consuming the
  first window. No similarity comparison establishes factual equivalence.
- If a complete first selection yields no publishable voice, inspect one more
  window of at most five candidates, with at most two additional windows shared
  across the entire section. Keep first-pass diagnostics. Translation recovery
  retains its existing section-wide item/call cap and global LLM cutoff.
- Prioritize identity recovery, then clearly named speech dates, before unrelated
  search matches. Production context recovery permits six requests within the
  existing 20-second context budget. Unsupported sources do not consume slots.
- Add supported major-publisher hosts and standard `article:published_time` /
  `datePublished` metadata. Dates require exact article identity and timezone;
  a conflicting H1, modification date or fetch time cannot establish freshness.
  HTTPS host validation, response limits and redirect refusal remain enforced.
- Share speech grammar between collection and processing. Historical baselines
  in current statements survive; dates attached to old speech still fail.
- Decode complete JSON or typographic-quote transport wrappers before translation
  validation. Unwrapped/malformed escapes remain rejected. Macro selected-source
  recovery has a bounded repair attempt; other sections keep their call budget.
- Recognize equivalent purpose modality, numeric upper-bound syntax, and market
  direction grammar. Changed upper bounds, quantities, dates, states or direction
  still fail. Repair prompts explicitly preserve source identifiers.
- Presentation version 17 removes structured navigation tails after publisher
  cleanup and repeated numeric standfirsts only when the reporting body repeats
  every heading quantity. Unique heading facts and product identifiers survive.
  Versions 1–16 retain historical replay contracts.
- Exclude financial changes, percentages, dates and metric labels from named
  product extraction, fixing the displayed `英伟达 Up 500` judgment subject.
- Sentiment permits verified movement context without treating it as the cause
  of the level score. Combined weights and computed score roles are checked;
  unsupported change-based explanations still fail. Score/model weights unchanged.
- Quality details distinguish the user-provided reference date from the unknown
  official observation date. Record nested runtime timeouts as structured events,
  resetting per execution; retain existing outer-stage diagnostics.

## Verification and limits

Targeted tests include fresh and old mirrored reports, wrong speakers, exact
article identity, bounded backfill, preserved first diagnostics, malformed
translation transport, changed quantities/states, retained named products,
historical presentation replay, unchanged sentiment scoring and nested timeouts.
The repository suite includes mock SMTP delivery and idempotency regressions.

Offline replay uses the actual archived publication mappings and rejected
translations of sources already selected by the production macro pass. It
recovers five previously rejected translations; final macro output remains three
paragraphs with six source links. Company mappings all replay successfully, and
the judgment subject becomes `英伟达`. Full HTML is 95,488 bytes. Network calls=0;
SMTP calls=0. No dates or speeches were invented to fill Voices.

Browser access to the local replay was denied by browser security policy;
browser/email-client visual acceptance is not established in this turn.

Source metadata may still be unavailable, paywalled or demonstrably old. Such
entries remain excluded; the changes do not guarantee daily qualifying speech.
The remaining archived ICC candidate has unsupported identifier/state details,
and another candidate fails entity order, so neither is forced into publication.
No fresh upstream retrieval or complete independent semantic verification of
every source article was performed. Valuation/cache expiry, buy rules, recipient
configuration, schedule and delivery controls are unchanged. No real email sent.
