# Editorial recall repair — 2026-10-01

## Observed regressions

The Oct 1 archived source mappings exposed two false rejections in the shared
contract release: a complete OpenAI agent-security event was treated as a noun
fragment because its predicate was not enumerated; a valid TSMC translation was
rejected because a background investment clause appeared before the new location.

## General repair

- Fragment detection requires an observable, bounded nominal-heading structure.
  An unfamiliar verb, a product noun, or the absence of a known event is no
  longer sufficient to reject an otherwise complete source sentence. Existing
  publication evidence checks still run. Recognized nominal headings still use
  a complete sentence from the same source when available.
- Known localized names are compared by occurrence, not textual order. Explicit
  monetary amounts are additionally associated with the nearest localized name
  within their clause; ties remain ambiguous. Reordering whole clauses may pass;
  swapping amounts between locations does not. This bounded check does not claim
  to parse all possible geographical relationships.
- The shared tentative-state pattern recognizes consider/considers/considered/
  considering consistently. Tentative investment cannot become an executed fact.

## Validation and boundaries

27 additional regression cases cover different issuers, unfamiliar predicates,
Chinese complete sentences, clause reorderings and monetary mutations. Both
actual archived stories pass through grounded_text with their original evidence
mapping, not merely a headline helper. The prior nominal-fragment, incorrect
entity, document-type, numerical, negation and event-state cases remain active.

Full suite: 2,334 passed. Ruff and diff whitespace checks passed. No SMTP call.
The archived fixture has 11 selected source mappings, not the full historical
candidate pool. This verifies recovery of the two observed false rejections; it
must not be described as a production-wide pass-rate measurement or a guarantee
of any daily story count. Tests protect valid inclusion as well as invalid
exclusion; news significance and available source evidence still determine output.
