# V1 — Naive Prompt (baseline for comparison)

This is the "naive" prompt behind `output/v1_naive_output.md`. The raw sales CSV is handed
directly to Claude — no deterministic engine, no business rules, no precomputed facts payload.
The point of this experiment is to see what happens when an LLM is asked to do arithmetic and
business judgment on its own, and to keep a real, unedited transcript of where it goes wrong.
That transcript is the evidence behind the architectural decision in the README: the LLM never
performs arithmetic. See `prompts/ITERATION_LOG.md` for the failure analysis.

No system prompt is used for this baseline — everything below is the single user message sent
to the API, with `<contents of data/mock_sales.csv inserted verbatim>` replaced by the actual
CSV file content at request time (see `src/main.py`'s `run_naive_baseline`).

## User message

You are a supply chain analyst for Manukora, a manuka honey and wellness brand. Below is our raw sales and inventory data for the last four months (M1 = December 2025 through M4 = March 2026), broken out by Shopify and Amazon channel.

Write an executive S&OP (Sales & Operations Planning) briefing for our leadership team. For each SKU, tell us: how demand is trending, whether we're at risk of stocking out, how many units we should reorder, and what the revenue opportunity looks like. Flag anything urgent. Keep it to something a busy executive could read in 5 minutes.

Here is the data:

<contents of data/mock_sales.csv inserted verbatim>

Give me the full briefing now.
