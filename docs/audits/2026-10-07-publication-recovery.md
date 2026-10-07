# October 7 publication recovery

The October 7 production artifact (run 37544067588, commit 2462120) contained a
cut-off Apple premium standfirst, context-free macro excerpts, an unlabelled TSMC
investment forecast, a Berkshire stock purchase classified as a partnership,
empty voices/frontier sections, and seventeen valuation collector timeouts.
SMTP accepted all four recipients; delivery success did not imply content health.

## Final behavior

- Truncated financial predicates and short untranslated tails are rejected;
  a complete title remains available instead of hiding the entire company item.
- Macro standfirsts retain their own title when its amount, geography or
  antecedent would otherwise disappear. Separate attacks no longer inherit the
  previous article's ship/strait context.
- Fundraising is a distinct bilingual economic event. Raising money no longer
  requires a Chinese price-increase verb. Amounts, plans, and actual price raises
  remain independently checked.
- Quarterly investment previews are identified as analysis and visibly labelled.
  A separate realised purchase fact can still be retained from a mixed headline.
  Analysis does not become realised new evidence in the long-term ledger.
- Equity purchases/holdings receive a portfolio-allocation watchpoint, rather
  than implying negotiations or a strategic partnership.
- Voices use the existing seven-day editorial window in collection too. Only
  actually published facts enter sent-history deduplication. Specific ordinary
  statements can rank at score 3; a source-bound statement may recover when the
  model rejects every item or ranking fails. Empty generic slogans, incorrect
  speakers, reversed meanings and known old reports remain ineligible.
- Speaker enrichment supports additional publishers and rotates people within
  each priority tier. When an approved media source explicitly names the speaker
  but its original date cannot be read, the statement may appear as recent media
  reporting, with its report date and an explicit notice that the speech date
  was not independently confirmed. It is not relabelled as today's new speech.
  Stored bindings replay this exact evidence tier; known old/future/invalid
  original dates cannot use it.
- A validated signal clause embedded naturally in the introduction can be
  recovered without forcing a JSON transport placeholder. Subject/category
  checks still reject invented buy actions or reference-line facts.
- Wire promotions and podcasts do not consume valuation report read slots.
  Repeated rate limits/timeouts have a fifteen-minute cooldown within one
  provider instance; completed sources still receive the late check, ordinary
  transient failures may retry, and a new run starts fresh. Failure timestamps
  do not advance while cooling down. Known newer official dates survive timeout
  diagnostics and appear beside dated historical values.

## Evidence and boundaries

The full production-path preview 37560336629 completed without SMTP and restored
three voices (Jensen Huang, Lisa Su, Sam Altman). It also exposed a truncated
listing credit on Lisa Su's headline, a partly untranslated bond sentence,
Russian public-health news labelled as Ukraine-war news, and older metadata
dates being called newer estimates. These cases are retained in a second fixture.

The final follow-up recovers complete statements before nominal exchange credits,
requires ordinary predicates to be translated, leaves rolling-page date labels
out of macro facts, and classifies explicit public-health reporting separately.
Update warnings compare actual valuation dates rather than declaring every quote
page date new. Already verified numerical estimates use a cheap exact-issuer
update-date/report-directory check when both show no newer information. New,
unknown or unresolved updates still require actual reading; the numerical
verification clock never advances from a metadata-only request. Timeout reasons
and outer-watchdog snapshots retain the cooldown even when a lower-level source
failure has already been logged.

`tests/fixtures/october7_publication_sources.json` retains the actual public
source packets and selected/rejected speaker candidates, without recipients or
credentials. Positive recovery and negative amount/identity/date cases replay
through the same publication bindings and final verifier.

A local source-only check still received Yahoo HTTP 429 for the newer COST and
MCO report pages. Latest inaccessible values are not invented. MCO's existing
user-specified 540 reference is preserved. QQQM and Pop Mart keep their dated,
qualified fallback policy and existing valuation formula; reading source data
later does not change a report's real data date. A full production-path preview
will be run with `preview_only=true`, without SMTP delivery or production dedup
state writes.
