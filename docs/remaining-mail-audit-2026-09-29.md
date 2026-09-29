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
