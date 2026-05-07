# Frontier Labs Handoff

This is a handoff note for a new Codex/Claude session. It covers the OpenAI / Anthropic module that should appear as a small addendum at the bottom of "昨日动态".

## Current Repo State

- Judgment Ledger is already being implemented in the current worktree.
- Relevant dirty files may include:
  - `src/main.py`
  - `src/renderer/render.py`
  - `src/renderer/templates/email.html.j2`
  - `src/processors/thesis/`
  - `tests/processors/thesis/`
- Do not revert or overwrite those changes.
- Frontier Labs has not been implemented as an independent module yet.
- Existing OpenAI-related coverage only exists indirectly through:
  - key figures: 奥特曼 / Dario Amodei
  - official source supplements for figures
  - future Judgment Ledger evidence input hook

## Task

Implement a small "前沿模型 / Frontier Labs" addendum under the "昨日动态" section.

This module monitors only OpenAI and Anthropic in V1. They are not holdings and must not be added to `HOLDINGS`. The module exists because their major changes can affect current holdings indirectly, especially MSFT, GOOG, NVDA, TSM, and AMD.

## Product Principle

Frontier Labs is not an AI news feed.

It answers one question:

> In the last 24 hours, did OpenAI or Anthropic have a major development that materially affects the user's holding-chain judgment?

Default behavior should be silence.

## Frontend Placement

- Place at the bottom of "昨日动态".
- Do not put OpenAI / Anthropic into the holding company loop.
- Render only when there are qualified items.
- Max 2 items total.
- Max 1 item per lab.
- Visual hierarchy must be lower than the holding company updates.
- No cards, no dashboard styling, no icons, no large module feel.
- Use existing email template style: table rows and inline styles, compatible with QQ Mail / Outlook / Gmail.

Suggested heading:

```text
前沿模型
Frontier Labs
```

Suggested item style:

```text
OpenAI｜新增数据中心合作，若落地将继续支撑云与 GPU 需求。
Anthropic｜企业模型收入披露提速，强化大模型商业化仍在早期的判断。
```

## Scope

V1 labs:

- OpenAI
- Anthropic

Do not add in V1:

- xAI
- Perplexity
- Mistral
- Cohere
- Meta AI
- DeepMind as a separate lab

Reason: keep this small and high signal.

## Data Sources

OpenAI:

- Official RSS: `https://openai.com/news/rss.xml`
- Google News RSS query: `"OpenAI" when:1d`
- Optional Google News query: `"Sam Altman" OpenAI when:1d`, but avoid duplicating the key figures module if possible.

Anthropic:

- Google News RSS query: `"Anthropic" when:1d`
- Optional Google News query: `"Dario Amodei" Anthropic when:1d`

Do not hard-crawl complex dynamic sites in V1. If Anthropic official news has no stable RSS, use Google News only.

## Suggested Files

Add:

- `src/collectors/frontier_labs.py`
- `src/processors/frontier_labs_filter.py`
- tests, preferably:
  - `tests/test_frontier_labs.py`
  - or `tests/processors/test_frontier_labs_filter.py`

Modify:

- `src/main.py`
- `src/renderer/render.py`
- `src/renderer/templates/email.html.j2`

Important: these files may already contain Judgment Ledger edits. Read and integrate carefully. Do not revert unrelated changes.

## Data Models

Suggested collector dataclasses:

```python
@dataclass
class FrontierLab:
    name: str
    queries: list[str]
    official_feeds: list[str]
    related_tickers: list[str]

@dataclass
class FrontierItem:
    lab: str
    title: str
    snippet: str
    published_at: datetime
    url: str
    source: str
    source_type: Literal["official", "google_news"]
    related_tickers: list[str]
```

Suggested processor output:

```python
@dataclass
class FrontierKeyPoint:
    lab: str
    text: str
    related_tickers: list[str]
    source_url: str
    source_name: str
    score: int
```

## Collector Rules

- Fetch official feeds and Google News RSS.
- Keep only last 24h.
- Basic title / URL dedupe.
- Use a separate pushed state:
  - `state/pushed_frontier_labs.json`
- 7-day dedupe.
- Fetch should return `(bundles_or_items, pending_pushed)`.
- Commit pushed only after email send succeeds, mirroring figures behavior.
- Per lab, pass at most 8 candidates to the LLM processor.
- Any source failure logs warning and does not block the email.

## Importance Filter

New processor: `src/processors/frontier_labs_filter.py`

Use the shared `LLMClient`.

The LLM must decide:

- whether the item is a major development
- whether it affects one or more current holding-chain tickers
- a score from 1 to 5
- a concise Chinese summary
- related tickers
- cross-source merge if multiple sources describe the same event

Display threshold:

- score >= 4
- related_tickers must not be empty
- max 2 total
- max 1 per lab

High-quality developments:

- new model generation that changes competition or compute demand
- major funding, revenue, monetization, or enterprise adoption
- major cloud, chip, data center, or compute partnership
- material AI capex / training / inference cost information
- safety, governance, regulation, or legal change with clear holding-chain impact
- clear impact on MSFT / GOOG / NVDA / TSM / AMD

Low-quality developments:

- minor product updates
- tiny benchmark wins
- developer tooling noise
- KOL / analyst commentary
- unverified funding rumors
- personnel gossip
- social media drama
- generic "AI is important" statements
- brand promotion without holding-chain impact

Suggested LLM output format:

```text
▦ N: yes | score=5 | tickers=MSFT,NVDA,TSM | <一句摘要>
▦ N,M: yes | score=4 | tickers=MSFT,GOOG | <合并后的摘要>
▦ N: no | score=2 | <淘汰原因>
```

Parser rules:

- yes + score >= 4 + non-empty tickers: keep
- yes but score missing: drop
- yes but tickers missing: drop
- score < 4: drop
- no: drop
- unsafe URLs: drop

## Rendering

`render_email` should accept `frontier_labs_items` or `frontier_labs_summary`.

Template:

- render only if non-empty
- place under company news / 昨日动态
- item format: `LabName｜summary [source]`
- source links must use existing `safe_url`
- no empty state
- no cards or new palette

## Judgment Ledger Integration

Frontier Labs is a fact layer.

Judgment Ledger is a judgment layer.

In V1, Frontier Labs output may be passed into thesis extractor as `frontier_labs_events`, but do not tightly couple them. If Judgment Ledger is unstable or still under review, implement Frontier Labs independently first and leave a clean integration hook.

## Cross-Module Duplication

Key figures and Frontier Labs may both see OpenAI / Anthropic news.

V1 acceptable:

- light duplication is tolerated if the content has different framing:
  - key figures: "person said"
  - Frontier Labs: "lab/company event"

Preferred:

- avoid identical wording in both modules
- if same URL/event is already displayed in key figures, Frontier Labs processor should be strict and only keep it if company-level impact is distinct

## Tests

Collector tests:

- OpenAI RSS parses.
- Google News RSS parses via mocked feed.
- 24h filtering works.
- URL/title dedupe works.
- pushed state dedupe works.
- fetch does not commit pushed early.
- source failure does not block other sources.

Processor tests:

- score >= 4 with tickers keeps.
- score < 4 drops.
- missing score drops.
- missing tickers drops.
- no rows drop.
- unsafe URLs drop.
- same-event merge works.
- max 1 item per lab.
- max 2 total.

Renderer tests:

- no items means no Frontier Labs block.
- 1 and 2 items render.
- more than 2 gets truncated.
- HTML escapes text.
- links use `safe_url`.
- existing "昨日动态" rendering remains intact.

Main integration tests:

- LLM failure makes module empty, email still sends.
- email success commits pushed.
- email failure does not commit pushed.

## Acceptance Criteria

- OpenAI / Anthropic are not added to holdings.
- The addendum appears only under "昨日动态" when high-signal items exist.
- Most days it should not render.
- Max 2 lines, max 1 per lab.
- Every rendered line must connect to current holdings' judgment chain.
- Existing modules and tests remain green.

## Suggested New Session Prompt

```text
We are in /Users/zhukaiyuan/Documents/projects/daily-market-brief.

Please implement the V1 "前沿模型 / Frontier Labs" addendum under the "昨日动态" section.

Important context:
- Judgment Ledger is currently being implemented and may have dirty changes in main.py, render.py, email.html.j2, src/processors/thesis/, and tests/processors/thesis/.
- Do not revert or overwrite unrelated dirty changes.
- Frontier Labs has not yet been implemented as an independent module.
- Do not add OpenAI or Anthropic to HOLDINGS.

Use docs/frontier_labs_handoff.md as the implementation spec.

Implement:
- collector for OpenAI / Anthropic
- LLM importance filter
- separate 7-day pushed state
- render integration under 昨日动态
- tests

Keep frontend extremely restrained: no cards, no icons, no empty state, max 2 lines.
Run pytest before final.
```
