# output/history/

Superseded briefing artifacts, kept for evidence of the prompt's evolution — not deliverables.
The current deliverables live one level up: `output/sop_briefing_2026-03.md` and
`output/facts_payload_2026-03.json`. See `prompts/ITERATION_LOG.md` for the full story behind
each file.

- **`v1_naive_output.md`** — the naive prompt (raw CSV, no engine, no facts payload), real and
  unedited. Shows what an unassisted LLM gets wrong on its own arithmetic.
- **`sop_briefing_2026-03_UNVERIFIED.md`** — the first real run of prompt v2. Numeric validation
  caught two hallucinated dollar figures before this reached an executive; kept as evidence the
  validator actually works, not just that it exists.
- **`sop_briefing_2026-03_v2.md`** — v2's output before six accuracy corrections (tension-flag
  scope, leaked field names, a wrong month label, a self-contradicting superlative, table
  ordering, circular reasoning).
- **`sop_briefing_2026-03_v3.md`** — after those six corrections, but before the Stock-at-Risk
  list was filtered to exclude overstocked SKUs — it still showed MGO 100+ 250g as "at risk"
  three sections before recommending its inbound PO be deferred for being overstocked.
