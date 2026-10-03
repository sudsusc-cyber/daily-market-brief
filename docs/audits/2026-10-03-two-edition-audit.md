# October 2–3 final-mail audit

Audited actual archived HTML, plain text, source mappings, rejection diagnostics,
SMTP logs and later same-edition runs at main `06c6d35`.

| Edition | Main run | SMTP acceptance (Beijing) | HTML bytes |
| --- | --- | --- | --- |
| 2026-10-02 | 36938382437 / attempt 1 | 07:06:53, full, 4 accepted / 0 refused | 66,386 |
| 2026-10-03 | 37075447875 / attempt 1 | 07:07:59, full, 4 accepted / 0 refused | 90,316 |

Later runs 36952323830 and 37085382725 detected the accepted edition and skipped
sending. No send was triggered for this audit. Recipient configuration, schedule,
valuation recipes, strategy, freshness limits and the 3-paragraph macro cap are unchanged.

## Reproduced defects and repairs

- A selected Altman statement was rejected solely because Chinese moved the
  named speaker before the claim and the guard did not recognize `将继续`.
  Separate syntactically bound attribution from claim actor/object ordering;
  still reject a different speaker (including the same surname), swapped claim
  actors and removal of future modality.
- Prospective lending headlines (`to lend`) were treated as completed statements,
  and an `EXCLUSIVE:` masthead was mistaken for a technical identifier. Recognize
  the future construction and paired masthead translation. Also bind `up to` /
  `最高` / `至多` / `最多增派` to the quantity; recovering coverage must not lose ceilings.
- Curly-apostrophe negative contractions (`isn’t`) were not equivalent to ordinary
  contractions. Preserve both positive/negative polarity across punctuation variants.
- A slash-delimited wire dateline with a missing subject reached the Microsoft
  body. Reject observable subjectless fragments and recover the intact factual
  title. Prefer a compact factual title when the alternative is a promotional
  corporate description. Source fields remain immutable.
- Company prefix removal produced `Inc 旗下的 Google` and `支持的…`. Version 11
  presentation consumes complete legal suffixes only for a direct predicate;
  ownership, modifier and coordinated subjects remain intact. Versions 1–10
  remain replayable. Add qualified TW/OTC listing grammars without removing
  ordinary parenthetical acronyms.
- Stock record highs and broker recommendation lists bypassed the operating-news
  gate. Reject price-only/recommendation material while retaining a real operating
  announcement in the same article. Remove an editorial second sentence from a
  two-sentence headline only when a complete first factual sentence survives.
- Smart-home coverage selected a metaphorical headline despite an actual partner
  in the following source sentence. Keep the exact adjacent source interval when
  an anaphoric second sentence supplies the operating event and the first supplies
  the holding identity. No invented antecedent or paraphrase is introduced.
- October 3 jobs data, dollar reaction and Treasury reaction occupied all three
  macro paragraphs. Group explicitly linked reactions with a unique same-domain
  data-release anchor. Keep different jurisdictions, explicit months, unrelated
  catalysts and ambiguous multiple anchors separate; keep every grouped source.
- The existing long-term product matcher could treat `April 2027` as a product.
  Calendar/fiscal grammar is excluded from product identity; genuine named models
  remain eligible. This defect came from the October 1 resend, not these two editions.

## Dependency audit

PR CI identified eight advisories in the existing locked `pypdf 6.16.2`:
PYSEC-2026-4153 through PYSEC-2026-4160. Raise the minimum and lock to 6.19.0,
which covers all reported fix versions, without ignoring any advisory or changing
other dependency versions. The upstream [changelog](https://pypdf.readthedocs.io/en/stable/meta/CHANGELOG.html)
documents bounded parsing/security fixes. Add a real two-page PDF extraction
integration test through the production official-report reader.

## Verification

27 new regression cases include immutable October 2/3 source mappings, full
company-publication replay, actual selected-voice recovery, macro grouping and
positive/negative preservation cases. Offline replay retains 15/10 company
sources (only the broker recommendation / price-only story is removed), and
7/3 macro sources. The three October 3 macro reports form one paragraph.
New translations used by offline replay are explicit test inputs checked by the
production guard, not claims about a newly executed production LLM response.

Final local validation: 2,383 tests passed; Ruff and `git diff --check` passed.
Production-renderer news previews were inspected in the browser. No SMTP was
called by preview or tests. Existing full-template regression coverage is retained.

## Evidence limits and upstream state

The two mood scores recompute to 49.7 and 53.0 from the archived deterministic
weights. Both full emails were below the 98,304-byte warning threshold. October 2
Hong Kong observations dated September 30 correspond to the October 1 holiday.
QQQM was explicitly carried from September 30; failure to obtain a newer verified
NAV is not repaired by relabelling the snapshot or changing its age. Other report
values were likewise labelled with their real source dates.

The two Roslansky headlines use current/former LinkedIn-chief descriptions. This
patch does not erase their differing qualifiers on text similarity alone. Those
source descriptions still need editorial reconciliation if further consolidation
is desired. The October 3 lack of selected voices is distinct from the October 2
translation failure; it is not automatically converted into a fabricated quote.

This audit verifies the archived publication pipeline and bounded source features,
not the truth of every upstream article or full semantic equivalence of arbitrary
translations. Upstream HTTP blocks, publication delays and genuinely unsupported
candidates remain visible in audit records. Correctly used deterministic mood and
intro fallbacks are not SMTP failures.
