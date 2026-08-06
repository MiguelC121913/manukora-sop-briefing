# Part 2 — Daily Morning Intelligence Brief

## What gets built

A daily brief surfacing the three things Manukora's leadership most needs to know today — not a
dashboard, just three ranked, explained signals, each answering "so what?" It reuses Part 1's
split (deterministic detection, LLM narrates only) but runs daily against yesterday's data instead
of monthly against a four-month window.

## Data sources

**Shopify Admin GraphQL API** (orders, inventory, checkout events) is the baseline — Manukora's
DTC channel of record. **Amazon SP-API** is the second source, chosen over Klaviyo or Cin7: it's
the only one of the three that produces a same-day, dollar-material signal Shopify alone can't
see — Buy Box loss and velocity drops are immediate revenue events, not slow-moving trend data. It
also extends Part 1's Shopify-vs-Amazon divergence logic to a daily cadence.
Klaviyo and Cin7 are natural later additions, but neither has an Amazon-style same-day signal.

## Pipeline

```
cron (fixed schedule) → fetch Shopify + Amazon (parallel) → normalize to
  {source, sku, metric, value, timestamp} → deterministic anomaly detection (no LLM,
  see Signal vs. Noise) → LLM narrates surviving signals only → Slack + email (timestamped)
```

Each source is queried once daily, matching the brief's cadence. Amazon SP-API's rate limits are
notoriously tight; the fetch stage needs quota-aware pagination and backoff retries, not a naive
single request. Suppression state (which signal fired, when, under what cause) lives in the same
time-series store as the 28-day baselines — one system, not two.

## Signal versus noise

Detection is **statistical, not threshold-based**: for each SKU/metric, compare today's value to a
trailing 28-day baseline for the *same day of week* (Monday compares to the last four Mondays,
not to Sunday), flagging when the deviation exceeds a configurable number of standard deviations —
not a flat "±X%" rule, which false-positives on volatile SKUs and misses slow bleeds on stable
ones.

Two guardrails keep the top-3 list honest:

- **Suppression** — a fired signal is marked seen and won't resurface for the same cause unless it
  materially worsens, or it clutters every brief until resolved.
- **Materiality floor** — signals rank by *dollar impact*, with a minimum threshold to qualify.
  A 40% swing on a $200/day SKU is real but small; a 6% swing on a $40k/day SKU is bigger in
  absolute terms and must outrank it — percentage-only ranking gets this backwards every time.

"Noise" is defined operationally, not a vague notion of "the model filters things": it's
any deviation still inside the day-of-week-adjusted baseline's normal range, plus any signal below
the dollar materiality floor no matter how large its percentage — a 90% swing on a $50/day SKU
still doesn't make the cut.

## The timezone constraint

Solved without tracking location or device activity — those require consent, add privacy
liability, and *infer* rather than *know*. Instead: the brief **generates on a fixed schedule tied
to data availability**, anchored to the timezone where Shopify/Amazon data actually settles for
"yesterday" — the US operating timezone, Manukora's primary market, not the NZ office — and is
**delivered on first-open**, not push-timed: it sits in Slack/email, timestamped with its data
freshness, so it reads correctly whenever it's opened. Optionally, an exec can
set a **declared preferred delivery window** in their own profile — explicit and revocable.
Inferring wake time from activity is the wrong trade even though it would "work": it repurposes
behavioral data without consent, breaks the moment a routine changes, and a simpler mechanism —
the freshness timestamp — already solves the real problem: trust in the data, not guessing a
schedule.

## Cost at Manukora scale

Per brief: ~4,000 input tokens (anomalies plus baseline context) and ~800 output tokens. At
roughly $3/$15 per million input/output tokens, that's **~$0.02–0.03/day,
~$1/month**. Hosting — a scheduled serverless function plus a lightweight time-series store for
the 28-day baselines — runs **~$15–25/month** on a standard cloud provider. All-in: **comfortably
under $30/month**, dominated by hosting, not inference.

## Failure modes

| Failure | Mitigation |
|---|---|
| Source API downtime | Ship a **degraded brief**: remaining stages run on whatever data is present, with an explicit "Amazon data unavailable" notice — never silently drop a source. |
| Partial data | A minimum completeness threshold per source; below it, excluded and flagged, never included with silent gaps. |
| LLM unavailable | Fall back to the raw ranked signal list, no narrative — a bulleted table still beats silence. |
| Alert fatigue | The suppression rule above, plus a hard cap of 3 signals regardless of how many crossed threshold. |
| **A confidently wrong number eroding trust permanently** | Same numeric-validation pattern as Part 1: every figure traces back to the detection payload, checked before delivery. Most dangerous failure — trust doesn't return at the rate it was lost. |
