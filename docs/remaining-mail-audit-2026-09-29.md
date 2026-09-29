# September 29 remaining mail audit

Scope: archived HTML/plain text and original-source mappings from formal run
`36502706162`, SHA `b85d5d95b17b3d4985813dde0bec38bd0f8c83fc`.
This is the application's sent artifact, not a retrieved inbox MIME copy. It is
not a sentence-by-sentence audit of historical run #230.

## Confirmed in the sent artifact

| Finding | Correction |
|---|---|
| Apple “legal bills” became legislative bills; a price headline hid the actual lawsuits | Select the complete case/patent verdict/appeal sentence; reject the demonstrated cost/legislation word-sense reversal |
| American Express RSS attached a publisher footnote (`worldwide1`) to the report and published `受理1` | Treat that excerpt as ambiguous; select a complete alternative title without deleting numbers from immutable evidence |
| A September 28 investing question repeated Mastercard's September 22 launch and fed a “new variable” | Exclude explicitly dated old-event investing recaps and their undated continuation clauses, including at original-source and thesis revalidation; retain explicit new updates |
| Macro included a local-colour crime article with no stated broader impact | Exclude this narrow category unless the source identifies policy/market/supply effects; retain actual geopolitical events |
| DXY displayed 101.2 / 101.0 alongside +0.27 | Display current/prior/delta with consistent two-decimal precision |
| Compilation time labelled as data cutoff; oldest valuation check labelled as one uniform check time | Use “编制时间” and “最早核验”; normalize compilation datetime to Beijing before rendering |

The recap rule is deliberately bounded: an investment-question/retrospective
headline, a leading event date over two days before **article publication**, and
no explicit today/yesterday update. It does not apply a stock daily-data calendar
to macro articles, interviews, FRED or other observation frequencies. It is not a
complete general event-date extractor. Fresh updates are preferred over old
background; scheduled future events and year-boundary cases are tested.

Previously merged fixes already address the Microsoft partner-award promotional
item, wire datelines/standfirsts, ticker clutter, macro grouping, missing Yahoo
reference lines and frontier partial-rejection status. They are retained.

## Additional reproduced pipeline defect

Figure filtering shared the frontier failure mode: one rejected selected article
could erase valid peers; source failures could disappear when prefiltering left
no candidates. It now keeps independently verified items, records content
rejections separately from processing/source failures, rejects conflicting
index decisions, retains partial observations after timeout, and bounds/redacts
error details. Full-entrypoint mocked SMTP tests verify one delivery and commit
only published source hashes.

The actual audited run's figure section had normal all-no decisions. This latent
bug was reproduced offline, not claimed to have occurred in that run.

## Verification and limits

Regression fixtures contain allowlisted original news fields and prior output;
no credentials or recipients. Tests cover original-source immutability, thesis
rejection, real duplicates versus changed facts, new updates, source outages,
partial outcomes, rendering and simulated delivery. Run the complete CI matrix
(Python 3.11/3.12), Ruff and `git diff --check` before merge.

Translation guards are deterministic bounded checks, not a proof of all
multilingual semantics. Unsupported candidates remain rejected. An externally
blocked valuation source can still require a truthfully dated retained value;
this audit does not relax data age, source validation or valuation rules. No real
SMTP delivery is part of this audit. Production validation uses `preview_only`.

## Production preview follow-up (run 36507985129)

The merged preview ran on `7131405`, with `delivery.status=not_sent` and
`smtp_calls=0`. Source/body/citation checks passed, but review found additional
presentation/state errors, so that preview was **not** accepted as the final result:

- Yahoo's unbounded requests started including September 29 pre-open/intraday HK
  bars. Both primary and fallback rejected them as newer than the September 28
  completed session. Bound both daily and weekly requests to local midnight
  following the last completed exchange session; retain identity, continuity,
  price and target-date checks. A server ignoring the requested end is still
  rejected. Live follow-up returned all 15 complete price/reference-line signals
  as of September 28, including Tencent and Pop Mart. No live quote/metadata
  substitution or change to Close adjustment basis or strategy.
- A Berkshire headline joined a buying pitch with an actual Lennar purchase.
  Select the complete purchase sentence only; retain the immutable headline in
  audit evidence and reject a pitch without an independent reporting sentence.
- A cancelled model release matched the generic product-commercialization
  watchpoint through the word “release”. Give cancelled/shelved/delayed launches
  their own neutral monitoring question; denial of cancellation is not a setback.
  The final rendering gate revalidates the selected watchpoint against the source.
- An identical OpenAI fact appeared in macro and frontier. Merge only when both
  original excerpt and displayed fact agree; preserve all validated source links
  in the macro paragraph, revalidate the rebuilt mapping, and commit the frontier
  hash only after that source actually appears in an accepted email. Different
  facts, quantities or states remain separate candidates.

## RSS wrapper follow-up (run 36509293133)

Production preview on `46c350e` had 31 current verified observations, four retained
valuations, zero missing observations and zero unresolved conflicts. HTML was
97,606 bytes and SMTP calls remained zero. The release-adjustment judgment now
matched the visible OpenAI cancellation news, and all 15 reference lines worked.

The final text audit additionally caught two RSS parsing defects:

- Figure quote screening inspected raw HTML. Quotes around `href`/`target`
  incorrectly qualified a corporate Nvidia statement as Jensen Huang's speech.
  Inspect rendered text only, reject explicitly corporate-attributed statements,
  and preserve real named-person/direct-quote markers (including “says”).
- Google News's rendered summary separated a known publisher with two NBSPs;
  its translation retained two spaces instead of a dash. Strip that exact
  publisher-tail form at presentation, preserving source fields and in-sentence
  attribution. Unknown names or ordinary single-space prose are not removed.

These exact archived wrappers and translation forms are regression fixtures.
The corrected archive replay retains content/source bindings and final-render
judgment validation. It is an offline revision of that preview's inputs, not a
new SMTP send or a claim that arbitrary future translations are infallible.
