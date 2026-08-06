# Prompt Iteration Log

## V1 — Naive prompt (`prompts/v1_initial.md`)

### Run details

- Model: `claude-sonnet-4-6`, real API call via `python -m src.main --naive`.
- **First attempt truncated.** `max_tokens=4096` was too low for a 12-SKU report; the
  response cut off mid-table (`stop_reason: max_tokens`) partway through the final dashboard.
  This was an engineering mistake on our side, not a model failure — raised to `max_tokens=8192`
  and re-ran. Everything below is analysis of that **complete, unedited** run.
- Full raw output: `output/v1_naive_output.md` (14,447 chars). Nothing in it has been edited,
  reordered, or cleaned up.

### Observed failures (checked against the Phase 2/3 golden table and `config/business_rules.yaml`)

**1. Growth-rate arithmetic is wrong even by its own stated method.**
The model computed a simple (M4 − M1) / M1 percentage per SKU. Recomputing that exact formula
from the raw CSV numbers it also transcribed correctly shows its stated percentages don't match:

| SKU | Correct (M4−M1)/M1 | V1 stated | Error |
|---|---|---|---|
| MGO 100+ 250g | 11.7% | "+15%" | +3.3pp |
| MGO 263+ 500g | 23.0% | "+26%" | +3.0pp |
| MGO 514+ 500g | 30.7% | "+27%" | **−3.7pp (wrong direction)** |
| MGO 850+ 500g | 32.6% | "+30%" | −2.6pp |
| MGO 1700+ 100g | 33.9% | "+28%" | **−5.9pp (largest error)** |
| Bioactive Energy | 42.6% | "+37%" | −5.6pp |
| Bioactive Recovery | 46.8% | "+42%" | −4.8pp |

These aren't a different-but-defensible methodology (our engine uses compound monthly growth,
capped at 12%, which naturally reads differently) — they're arithmetic errors against the
model's *own* simpler method, stated with false precision ("+15%") right next to correctly
transcribed raw inputs. An executive has no way to tell which numbers on the page are solid.

**2. Propolis phase-out rule: ignored entirely — and actively recommends the wrong action.**
Nothing in the raw CSV encodes the phase-out plan (it's a `business_rules.yaml`-only fact), so
V1 had no way to know it, and treats Propolis like any other growing SKU: *"Reorder immediately.
Consider larger safety stock buffer given explosive growth trajectory."* This is close to the
worst possible recommendation for a SKU being wound down in Q2 2026 — restocking a product
about to be discontinued instead of managing a controlled exit. `rules.py`'s
`suppression_reason` mechanism exists specifically to prevent this class of error.

**3. Bioactive trend measured against M1 — violates the trend-baseline override.**
All three Bioactive growth percentages above are computed from the M1 baseline, despite M1
being spurious (the line launched mid-January, per `business_rules.yaml`'s
`trend_baseline_month: 2`). V1 had no access to that config and had no way to know M1 was
invalid — it just used the number that was there. This is exactly the failure mode that
motivated the loader's `data_quality_warnings` mechanism: without it, "the data was present" and
"the data was valid to use" get silently conflated.

**4. No revenue-at-risk prioritization — total revenue and at-risk revenue are conflated.**
V1 shows a "2-Month Revenue Opportunity" figure for all 12 SKUs, including fully-stocked ones
with zero reorder need (MGO 263+ 250g shows **$112,184** sitting in the same visual weight as
the critical SKUs, despite having ~4.6 months of cover and nothing at risk). The SKU-by-SKU
sections and the final dashboard are both left in original CSV row order — never re-sorted by
revenue, cover, or anything else. The closest thing to prioritization is a cover-threshold status
label (Critical / Top-Up / Monitor / Healthy), which is driven by cover, not revenue at risk. This
is the precise distinction `rules.py`'s `priority_reorder_list` was built to make explicit.

**5. Lead time handled inconsistently, and never for a genuinely new reorder.**
V1 does apply lead time correctly for the two SKUs that already had a PO with a known
arrival month in the CSV (MGO 850+ 250g, MGO 1700+ 100g) — a legitimate "stock at time of
arrival" calculation. But for every SKU with **zero** units on order, there's no
`supplier_lead_time_months` anywhere in the raw CSV to reason from (that's a
`business_rules.yaml`-only value), so V1 hedges instead of computing:
*"if lead time exceeds 6 weeks, this SKU goes out of stock..."* — a conditional, not a number.
This is the gap the "most important calculation" in Phase 2 (lead-time-aware reorder quantity)
exists to close.

**6. Analyst altitude, not executive altitude.**
Twelve near-identical multi-row tables plus a 12-row dashboard plus a strategic-takeaways
section — roughly 2,900 words. Not a 5-minute read. Every SKU gets the same depth of detail
regardless of whether anything needs to happen.

**7. Recommendations are ranges, not numbers.**
"1,500–2,000 units", "900–1,100 units" — an executive (or a purchasing system) can't act on a
range. Every number in our deterministic engine is a single integer, reproducible from the
same inputs every time.

### What V1 got right (not just where it failed)

- **No hallucinated inputs.** Every stock-on-hand, units-on-order, and retail-price figure
  transcribed from the CSV was correct in every SKU checked. The failures are in the *derived*
  numbers, not the raw ones.
- **Correctly used `Target_Months_Cover = 3` for MGO 1700+ 100g** — read directly from that CSV
  column, matching our engine's resolved policy value exactly, with no business-rules file to
  consult.
- **Correctly used the existing PO's lead time** (`Order_Arrival_Months = 2`) for the two SKUs
  that had one, and reasoned about the stock gap between now and arrival — a real (if partial)
  version of the lead-time logic our engine formalizes.
- **Flagged 6 of the 8 SKUs our engine flags for reorder** — real triage value even though the
  underlying quantities aren't trustworthy.
- **Decent business instincts in the prose**: the "the 500g format is systematically
  underinvested" and "the Bioactive line is our most exposed growth category" observations are
  the kind of pattern a human analyst would also flag. The narrative judgment isn't the problem —
  the arithmetic underneath it, and the missing business-rules context, are.
- Two errors run in **both directions**: V1 recommends a reorder our engine says isn't needed for
  MGO 850+ 250g (its own PO already covers it, at a longer horizon than V1's 2-month lookahead),
  and V1 recommends *no* action for Bioactive Immunity where our engine computes a real 800-unit
  need. It isn't a model that's simply "too cautious" or "too aggressive" — it's inconsistent, which
  is worse: a reader can't apply a correction factor to numbers that are wrong in unpredictable
  directions.

### Why this matters

Every number above that's wrong is wrong for the same underlying reason: the model had no
deterministic ground truth to work from, only a CSV and a request to reason from scratch. The
growth-rate errors are small in isolation, but they compound into reorder quantities that are
ranges instead of numbers, and — critically — into a completely absent understanding of a
business rule (Propolis's phase-out) that exists nowhere in the data a naive prompt can see. This
is the direct evidence for the architecture in the README: **the LLM never performs arithmetic.**
A deterministic engine computes every number here reproducibly from `data/mock_sales.csv` and
`config/business_rules.yaml`; the LLM's job (in V2) is only to explain numbers it's handed, not
to invent them.

---

## V2 — Structured, facts-grounded prompt (`prompts/v2_improved.md`)

Written directly against the seven failures above. Every V1 failure maps to a specific V2
constraint:

| V1 failure | V2 fix |
|---|---|
| Miscalculated growth %, invented ranges | Prompt states figures must be copied verbatim from the facts payload — never computed or estimated |
| Propolis phase-out ignored | Facts payload carries `suppression_reason`; prompt requires citing it whenever present |
| Bioactive measured against M1 | Facts payload's per-SKU notes flag the M1 exclusion; prompt forbids referencing M1 for those SKUs |
| Revenue opportunity vs. revenue at risk conflated | Prompt requires the two figures be explicitly labeled and never merged |
| Lead time inconsistent | Facts payload pre-computes the lead-time-aware reorder quantity; prompt requires using it as-is |
| Analyst altitude | Prompt fixes a required structure and a length ceiling |
| Ranges instead of numbers | Prompt requires every recommended quantity be the single integer from the payload |

See `prompts/v2_improved.md` for the prompt itself; it will be exercised end-to-end once
`src/narrative.py` and the facts-payload builder exist (next phase).
