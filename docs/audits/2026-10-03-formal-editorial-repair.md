# October 3 formal edition: editorial repair

Evidence: archived formal run 37116872096, attempt 1, sent at 18:41 Beijing.
This audit uses the sent HTML/text, source mappings and logs, not inbox rendering
or independent verification of every upstream report.

## Findings and changes

- The introduction was mechanically assembled around a mandatory signal phrase.
  Generate one complete literary paragraph instead, with no current-portfolio
  assertion. Keep recent-publication repetition checks and seven natural fallback
  paragraphs. Historical signal phrases are used only to compare old prose.
- Remove the unwanted sentiment implementation sentence in the fallback and at
  the display boundary, including old cached/model text. Keep score computation.
  Bind explicit weight and score numbers to their computed roles to avoid false
  rejections, while rejecting changed numbers.
- Translation confused service loss with financial loss, reserve releases with
  product/person releases, and writing a check with cutting a payment. Normalize
  event-object roles on both language sides. Also distinguish reported data from
  product releases, support Chinese duration 两, and preserve low-probability
  statements without reversing their meaning.
- Recover all five archived macro translation false rejections. Tests also reject
  changed quantities, reserve-release status, loss/profit and dividend direction.
- Require a company-specific operating excerpt from a roundup; don't republish a
  whole weekly voices headline under each company. Filter personality disputes
  without operating evidence. Identical same-source facts use one jointly labelled
  row, with original evidence and company attribution retained.
- Include adjacent original sentences for dependent relief/reaction excerpts, so
  bond background remains in both translation and topic classification. This
  rejoins the audited bond excerpt with the other bond story.
- Named-person attribution without quote marks remains eligible for voices.
  Separate directly attributed reporting from speculation about a person's view.
  Apply candidate limits after quote-marker qualification and audit every rule
  exclusion. Similar timing/company alone no longer instructs the model to merge
  distinct statements. Keep the existing freshness, evidence and quality gates.
- Localize configured full person names only in already Chinese prose, versioned
  for replay. Keep untranslated English from becoming publishable merely because
  a person's name was localized; preserve pre-presentation history for dedupe.

## Voice absence in this edition

The archived selection rejected a broker stock pick, an unnamed AMD executive,
a third party's praise of Pichai, Trump's statement, and previously published
Pichai/Altman events. These records don't establish a newly publishable statement
for every tracked person. The changes remove avoidable screening losses; they do
not fabricate a quote or guarantee a nonempty section every day.

## Validation boundary

The full offline suite uses mocked SMTP. The new fixture contains the five actual
rejected source excerpts and translations, without credentials or recipients.
An offline replay uses production section rendering inside the historical layout;
it is a presentation and recovery demonstration, not a new full live edition.
No real email is sent as part of this repair. Price strategies, valuation models,
recipient configuration, schedules and freshness limits are unchanged.
