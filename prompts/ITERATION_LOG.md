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

---

## V2 in production — a real hallucination, caught by the validator it was built to need

Once `src/narrative.py` existed (facts payload + post-generation numeric validation), V2 was run
against the real facts payload for real, via `python -m src.main --generate`. This section
documents what actually happened on that first run — not a hypothetical.

### First run: validation failed

```
Narrative validation FAILED:
Narrative contains figures not found anywhere in the facts payload (possible hallucination):
  - '$98,025' (parsed as 98025)
  - '$660,000' (parsed as 660000)
```

The unverified transcript is preserved at `output/sop_briefing_2026-03_UNVERIFIED.md`. Checking
both figures against `output/facts_payload_2026-03.json`:

- **`$98,025`** appeared in the headline as a hand-summed subtotal of "SKUs stocking out next
  month." No subset sum matches it — the closest real figures are
  `total_revenue_at_risk_monthly_usd = 181224` (all 7 reorders) or a manual sum of the 4 SKUs with
  `projected_stockout_month == 2` (**$97,505**, not $98,025). The model computed an aggregate
  itself, and even by its own apparent logic, got the arithmetic wrong.
- **`$660,000`** described "combined excess retail value" for the overstocked SKUs. The payload's
  actual `total_excess_retail_value_usd` is **$722,788** — the model's estimate understated the
  real figure by over $62,000.

This is exactly the failure the deterministic-engine architecture and the post-generation
validator exist to catch, and it did: neither figure reached an executive. The fix wasn't to
patch these two numbers — it was to close the gap that let them happen: the payload already
carried both `total_revenue_at_risk_monthly_usd` and `total_excess_retail_value_usd`, but the
prompt only told the model not to *invent* numbers, not that it must never *combine* payload
numbers into new ones. Added constraint 9 to `prompts/v2_improved.md`:

> Never sum, average, or otherwise combine multiple payload figures into a new number... If you
> want a portfolio-level total, use the matching field under `portfolio_totals` verbatim.

Separately (unrelated to the hallucination, found by re-reading Phase 6's required shape against
the first pass's output), the required-output-structure section was rewritten from V2's original
5-section shape to the exact 6-section shape Phase 6 specifies (*The Decision / What Moved This
Month / Stock at Risk / Reorder Recommendations / Judgment Calls / Assumptions and Caveats*), with
explicit, config-driven instructions for the three judgment calls the brief names (Propolis exit,
overstock-and-stalling tension, 250g/500g capital imbalance) — phrased generically off payload
fields (`is_overstocked`, `trend`, pack-size naming), not hardcoded to specific SKU names, matching
the same principle `rules.py` follows.

### Second run: clean

Re-ran with the strengthened prompt. Validation passed with zero unmatched figures. Saved to
`output/sop_briefing_2026-03.md` — the artifact referenced in the README as the final deliverable.
Both output files are kept in the repo: the failed run as evidence the validator works, the
passing run as the actual briefing.

---

## V2 → V3 — six corrections found on a close read of the V2 output

The V2 briefing (`output/sop_briefing_2026-03_v2.md`, preserved for comparison) passed numeric
validation cleanly, but a close read against the golden table and the business rules turned up six
more problems — none of them numeric hallucinations (the validator has no opinion on these), all
of them things an executive would notice. One required a business-logic change in `rules.py`; the
other five were prompt/payload problems. Fixed in that order.

### 1. `tension_flag` criterion — a business-logic change, not a prompt fix

The original criterion (Phase 3's own spec) gated the flag to the top 3 SKUs by
`revenue_opportunity_monthly`. On this month's real data that excluded MGO 100+ 250g — overstocked,
stalling, with a 2,000-unit PO still inbound — because it ranks #7 by revenue. That's the
portfolio's clearest tension case, and the rule as originally scoped couldn't see it.

Changed the criterion in `_compute_tension_flags` (`rules.py`) to: `is_overstocked` AND a
declining-demand signal (`trend == "stalling"` OR either channel's MoM negative) — no revenue
ranking at all. Verified against all 12 real SKUs that exactly one now fires (MGO 100+ 250g) and
no other SKU trips the broader condition; added/rewrote tests in `test_rules.py` for both the real-
data case and the boundary conditions (overstocked-but-healthy, stalling-but-not-overstocked, and a
low-revenue SKU firing to prove the rank gate is really gone). Documented the change and its
rationale directly in `rules.py`'s module docstring, not just here.

### 2. Raw payload identifiers leaking into prose

V2's output contained literal tokens like `priority_reorder_list`, `is_overstocked: true`, and
`phase_out_q2_2026_cover_above_30_days` in visible text — an engineer's document, not an
executive's. Added constraint 10: field names, keys, flags, and identifiers never appear in the
narrative, only their values translated into business language, with the exact
`phase_out_q2_2026_cover_above_30_days` string as the negative example to translate.

### 3. Bioactive baseline month — "February" in one section, "January" in another

Section 2 said "from their February baseline onward"; Section 6 correctly said "January 2026
onward" (M2 for this dataset really is January 2026 — M1 is December 2025). The model was
translating `trend_baseline_month: 2` into a calendar month itself and got it wrong in one of the
two places it tried. Fixed at the data layer, not just the prompt: added a `trend_baseline_label`
field to each SKU's payload entry (`_month_labels()` in `narrative.py`, deriving all four calendar
labels from `period.m1_label`) so the model never has to do that translation — and added constraint
11 requiring any month reference use a payload-provided calendar label, never a self-translated
number.

### 4. Internally contradictory superlative

Section 2 called MGO 514+ 500g's 11.4% "the strongest honey growth," then two sentences later
reported MGO 1700+ 100g — also a honey SKU — at 13.6%. Added constraint 12: no superlative
("strongest," "highest," "fastest," "best") unless verified against every SKU in the named
category; use a specific comparison instead if not certain.

### 5. Stock at Risk table sorted wrong — found *after* the "fix," a real structural bug

Section 5 correction asked for the table sorted by revenue exposed, descending. The first
re-generation still got it wrong: rows 5–6 ($26,764 and $23,554) were placed *before* rows worth
more ($34,316 and $29,477) — an inconsistent, partially-sorted order that a stronger worded
instruction alone hadn't fixed. Caught this by actually re-reading the regenerated table rather
than trusting that adding an instruction had worked.

The fix was architectural, not another sentence in the prompt: sorting a 12-row list correctly in
prose is exactly the kind of arithmetic-adjacent task this project's core principle already says
not to delegate to the model. Added `stock_at_risk_list` to the facts payload — pre-sorted by
revenue exposed, descending, in Python (`build_facts_payload`) — and changed constraint on Section
3 to require reproducing that order exactly rather than re-sorting it. Added a payload test
(`test_payload_stock_at_risk_list_sorted_by_revenue_descending`) asserting the exact expected order
against real data. Re-ran: correct on the first try once the model no longer had to do the sorting
itself.

### 6. Circular reasoning — needed two attempts to actually fix

The original complaint: MGO 850+ 500g's rationale cited "$109.99 retail" as a reason a stockout
would be "disproportionately costly" — circular, since the $109.99 unit price is already one of the
two numbers multiplied together to produce the revenue-at-risk figure in the adjacent column.
Added constraint 13 with a general description of the problem and re-ran.

**The first attempt didn't fix it.** The regenerated MGO 850+ 500g rationale still read "at $109.99
retail this is the highest unit-value SKU facing imminent stockout, making each lost sale
disproportionately costly" — nearly the identical sentence the correction was written against. A
general description of "don't be circular" wasn't specific enough to stop the model from reaching
for the same argument on the same SKU. Rewrote constraint 13 with the exact failing sentence quoted
as the negative example ("A rationale like '...' is exactly this mistake") rather than describing
the pattern abstractly. Re-ran: MGO 850+ 500g's rationale changed to "losing availability on the
premium 500g format risks customers trading down rather than waiting" — no price citation — and a
check of all 7 reorder rationales in the new output confirmed none of the others had drifted into
the same pattern either.

This is the clearest example in the project of a real gap between "the constraint is written down"
and "the constraint is followed" — worth keeping in the log precisely because it didn't work on the
first try.

### Net result

All six corrections verified against the actual regenerated `output/sop_briefing_2026-03.md`
(not asserted from the prompt text): MGO 100+ 250g's judgment call explicitly recommends deferring/
reducing its 2,000-unit inbound PO; no raw payload identifiers appear anywhere in the prose; the
Bioactive baseline reads "January 2026" consistently in every section that mentions it; MGO 1700+
100g's 13.6% is the only superlative claim made and it's the actual maximum among honey SKUs
(confirmed against the payload: next-highest is 11.4%); the Stock at Risk table is correctly sorted
$59,763 → $6,578; and all seven reorder rationales avoid restating price/quantity/revenue as if they
were independent arguments. Numeric validation passed clean throughout every regeneration in this
round. Full test suite: 67 passed.

The pre-correction V2 briefing is kept at `output/history/sop_briefing_2026-03_v2.md` (moved there
in a later cleanup — see below) so the v2→v3 evolution is visible in the repo, not just described
here.

---

## Two final adjustments — and a pattern worth naming

### 1. `stock_at_risk_list` needed a filter, not just a sort

After the v2→v3 corrections above, `stock_at_risk_list` (Correction 5's fix) listed all 12 SKUs,
correctly sorted — but Section 3 then showed MGO 100+ 250g "at risk" of stocking out in month 8
while Section 5, three sections later, explained that same SKU is overstocked and its inbound PO
should be deferred. Both statements were individually true and independently traceable to the
payload; together they read as a contradiction, because "at risk" implicitly means "should be
addressed," and an overstocked SKU with a stockout eight months out doesn't need addressing.

The original instruction for that list said "sort by revenue exposed" but never said what the list
should contain in the first place — an incomplete spec, not a prompt-following failure. Fixed at
the data layer: `stock_at_risk_list` in `build_facts_payload` now filters to SKUs projecting a
stockout within `stock_at_risk_horizon_months` (a new `business_rules.yaml` default, 4 months —
documented there against the longest supplier lead time in the dataset) **and** excludes anything
`is_overstocked`, before sorting. Verified against real data: exactly 8 SKUs remain (down from 12),
none of the four overstocked SKUs appear regardless of their stockout month or revenue size, and
Propolis Tincture — suppressed, but *not* overstocked — correctly still appears, because "at risk of
stocking out" and "not being reordered" are both true at once for a SKU on a controlled exit, which
is a different case from an overstocked SKU being wrongly labeled "at risk." Added
`test_payload_stock_at_risk_list_filtered_and_sorted` covering all three conditions against the
real payload. Regenerated: the contradiction is gone, and the model added a one-line note
explaining *why* Propolis is still on the list instead of treating it as an anomaly — the kind of
detail that's easy to get right once the underlying data no longer contradicts itself.

**This is the second time in this project a narrative-quality problem was fixed by moving the
computation into `build_facts_payload` (Python) instead of writing another prompt instruction** —
the first was the sort order in Correction 5. The pattern is the same one the whole architecture is
built on (metrics.py/rules.py compute, the LLM only explains) — it turns out to extend past pure
arithmetic to *any* structural decision (what belongs on a list, what order it's in). Worth stating
as a general rule for anything added to this project later: if a payload field asks the model to
select, filter, rank, or aggregate a set of items in prose, that's a signal that logic belongs in
`narrative.py`'s payload construction, not in the prompt.

### 2. `output/` reorganized

Moved `sop_briefing_2026-03_UNVERIFIED.md`, `sop_briefing_2026-03_v2.md`, the pre-filter briefing
(as `sop_briefing_2026-03_v3.md`), and `v1_naive_output.md` into `output/history/`, with a short
README there explaining what each file is and why it's kept. `output/` now holds only the current
deliverable (`sop_briefing_2026-03.md`) and its facts payload — no ambiguity about which file is
the actual submission versus evidence of how it got there.

### Net result

Full pipeline re-run after both changes: numeric validation passed clean, `pytest` 67/67, and
Section 3 no longer contradicts Section 5 — every SKU on the Stock at Risk table is one that
genuinely needs a decision, and every SKU held out of it (overstocked, or too far out to be
actionable today) is absent for a reason a reader doesn't have to go hunting for in a later
section.
