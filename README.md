# Manukora S&OP Briefing

Manukora's leadership needs a monthly read on inventory risk and reorder decisions without
digging through a spreadsheet themselves. This repository turns four months of Shopify/Amazon
sales and inventory data into a two-page executive Sales & Operations Planning (S&OP) briefing —
who's at risk of stocking out, what to reorder and why, and which judgment calls need a human.
Every number in that briefing is computed by deterministic Python before an LLM ever sees it, and
every number the LLM writes is checked back against that computation before the briefing is saved.

## Architecture

The core design decision: **the LLM never performs arithmetic.** A deterministic engine computes
every figure and hands Claude a JSON "facts payload" containing only numbers that are already
correct; Claude's only job is to explain what they mean. A second deterministic pass then verifies
that every dollar figure and unit quantity in Claude's prose actually came from that payload.

```
DETERMINISTIC — plain Python, no LLM
─────────────────────────────────────

  data/mock_sales.csv       config/business_rules.yaml
           │                          │
           └────────────┬─────────────┘
                         ▼
                  src/loader.py        schema validation, data_quality_warnings
                         │
                         ▼
                 src/metrics.py        growth, cover, stockout,
                         │             lead-time-aware reorder qty
                         ▼
                  src/rules.py         suppression, tension flags,
                         │             priority_reorder_list
                         ▼
               src/narrative.py        build_facts_payload()
                         │
                         ▼
     output/facts_payload_2026-03.json   ◀── numbers only, no prose crosses here


GENERATIVE — Claude, prompts/v2_improved.md
────────────────────────────────────────────

     reads the payload + system prompt
     writes narrative prose — computes NOTHING
                         │
                         ▼
            raw briefing text (Markdown)


DETERMINISTIC — plain Python, no LLM
─────────────────────────────────────

     assert_narrative_numbers_verified()
     extracts every $ figure and "N units" claim,
     fails loudly if one isn't in the payload
                         │
                         ▼
         output/sop_briefing_2026-03.md
```

If the LLM hallucinates a number, the verification step catches it before it reaches a reader —
this isn't hypothetical; it happened during development (see [Where the AI helped and where it
was wrong](#where-the-ai-helped-and-where-it-was-wrong)).

**A generalization of that principle, arrived at empirically rather than designed upfront:** twice
in this project, independently, a narrative-quality problem turned out not to be fixable by
writing a stronger prompt instruction — it needed the decision moved into Python inside
`build_facts_payload()` instead. First the sort order of the Stock at Risk table (asking the model
to sort a 12-row list in prose produced an inconsistent order even after being told explicitly how
to sort it); then which SKUs belong on that table in the first place (an overstocked SKU with a
distant stockout month kept appearing as "at risk" even after the model was told what "at risk"
should mean). Neither fix was planned; both were found by reading actual generated output against
real data and noticing the instruction wasn't reliably followed. The principle this project is
built on — the LLM never performs arithmetic — turns out to generalize past arithmetic: the model
also shouldn't be trusted to sort, filter, or compose a list correctly in prose. Any output that
requires selecting or ordering a set of items belongs in the facts payload, computed once in
Python, not asked for again in every generation.

## Setup and run

Verified end-to-end from a clean virtual environment.

```bash
git clone https://github.com/MiguelC121913/manukora-sop-briefing.git
cd manukora-sop-briefing

python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS/Linux

pip install -r requirements.txt

cp .env.example .env            # then add your ANTHROPIC_API_KEY inside
```

Run the tests (67 tests, fully deterministic — no API key or network access needed):

```bash
pytest -v
```

Run the real pipeline (requires `ANTHROPIC_API_KEY`; makes one API call to `claude-sonnet-4-6`):

```bash
python -m src.main --generate
```

This writes `output/facts_payload_2026-03.json` and, once numeric validation passes,
`output/sop_briefing_2026-03.md`. Console output shows every data-quality, metrics, and rules
warning surfaced along the way.

Optional — regenerate the naive baseline experiment from Phase 5 (real API call, no engine):

```bash
python -m src.main --naive       # writes output/history/v1_naive_output.md
```

## Assumptions

Every number in this pipeline is either computed from `data/mock_sales.csv` or governed by a
value in `config/business_rules.yaml`. Where the brief didn't specify a real business input, we
made an explicit, documented assumption rather than a silent one:

- **Supplier lead times — 2 months default, 3 months for MGO 1700+ 100g.** Two different things
  here, kept deliberately separate. That MGO 1700+ 100g specifically has a longer lead time than
  the rest of the portfolio is *given*, not inferred by us — `business_rules.yaml`'s own rationale
  for that SKU's override states it directly: "Premium price point and longer supplier lead
  times." The *specific number*, 3 months, is our assumption: the brief says "longer" without
  giving a figure, so we set it one increment above the 2-month default. That default itself comes
  from the only lead-time signal in the raw data — `Order_Arrival_Months` on already-placed POs,
  which ranges 0–2 months across the dataset — set at the observed ceiling. Notably, MGO 1700+
  100g's own `Order_Arrival_Months` is also 2, the same as everything else at the ceiling, so its
  3-month figure isn't derived from that column at all; it's a separate override chosen
  specifically to operationalize the qualitative "longer" the business rules already assert for
  this SKU. A real deployment would replace both the default and this override with actual
  supplier quotes.
- **12% monthly growth cap.** Unconstrained compounding of the raw growth rates observed in this
  data (up to 13.2% monthly, Bioactive Blend Recovery) over the 12-month demand path used for
  reorder sizing produces implausible figures — a real product with real production and logistics
  constraints doesn't quadruple in a year off a single month-over-month reading. 12% sits just
  below the raw range actually observed (11.6%–13.2%), aggressive enough not to understate a
  genuinely fast-growing new line, but bounded enough to keep the 12-month projection defensible.
- **M4 as the cover baseline, compound growth for sizing — both reported, not collapsed into one
  number.** `baseline_demand` is the actual, unprojected M4 sales figure — useful for sanity-
  checking cover math against what really happened last month. `projected_demand` (M4 grown one
  more month at the capped rate) is what actually sizes the reorder, because ordering against last
  month's number under-buys for a SKU that's genuinely accelerating. Both appear in the facts
  payload and, where relevant, in the briefing, so a reviewer can see the forecast without losing
  the plain fact it started from.
- **100-unit order rounding as an MOQ proxy.** The brief gives no real minimum order quantities.
  Every reorder is rounded up to the nearest 100 units (`order_rounding_units` in
  `business_rules.yaml`) as a stand-in for a case-pack or MOQ constraint a real supplier would
  impose, and because a purchasing team can act on "1,500 units" far more easily than "1,247."
  This is explicitly a placeholder — real MOQs vary by SKU and supplier and should replace this
  uniform rule in production.
- **Retail list price as the revenue basis.** `Retail_Price_USD` — full list price — is the only
  price data in the brief, so every revenue figure (`revenue_opportunity_monthly`,
  `revenue_at_risk_monthly`) is computed from it. This materially overstates realized revenue:
  Amazon referral and FBA fees (commonly ~15% referral plus fulfillment costs) and any Shopify
  discounting mean actual net revenue is meaningfully lower than what's reported here. Both the
  facts payload and the generated briefing's own "Assumptions and Caveats" section flag this
  explicitly so list-price revenue is never mistaken for a P&L figure.

## Business logic

**Prioritization ranks by revenue *at risk*, not revenue in total — and those are different
fields on purpose.** `revenue_opportunity_monthly` is a SKU's overall monthly revenue size,
computed for every SKU regardless of stock position. `priority_reorder_list` is a much narrower,
separately-computed list: only SKUs where current inventory is actually insufficient against
policy (`final_reorder_quantity > 0`, and not held out for a tension flag — see below), ranked by
how much of that revenue is genuinely exposed to a stockout, descending.

The distinction matters concretely: MGO 263+ 250g is the single highest-revenue SKU in the entire
portfolio (~$59,763/month) but carries ~4.5 months of cover against a 2-month target — it has
revenue opportunity and *zero* revenue at risk, and is correctly absent from the reorder list
despite being bigger than every SKU on it. A ranking built on total revenue instead of at-risk
revenue would have put a fully-stocked SKU at the top of an "urgent" list.

Two further layers sit on top of the raw math (`src/rules.py`):

- **Suppression** — a SKU can compute a positive reorder quantity and still not get reordered, if
  it's on a controlled phase-out plan (Propolis Tincture, Q2 2026) and current cover is still
  above the exit threshold. The reorder is forced to zero with a machine-readable
  `suppression_reason`, and the SKU still appears in "Stock at Risk" — a controlled exit and an
  ordinary stockout are different situations, and the briefing is expected to explain which one
  it's looking at, not just report a number.
- **Tension flag** — a SKU that is both overstocked and showing a declining-demand signal
  (stalling trend, or either sales channel's month-over-month growth negative) gets flagged and
  held out of the reorder list even if its raw math says otherwise; the recommendation becomes
  deferring or reducing its *inbound* PO instead of ordering more.

## Prompt stack

Three real iterations, not a single polished prompt written in hindsight — see
[`prompts/ITERATION_LOG.md`](prompts/ITERATION_LOG.md) for the full detail, including every
number that was wrong at each stage and why.

| Version | File | What it does | What changed and why |
|---|---|---|---|
| **v1** | `prompts/v1_initial.md` | Raw CSV + a one-paragraph ask, straight to Claude. No engine, no facts payload. | The deliberate anti-pattern: run once for real (`output/history/v1_naive_output.md`), used to demonstrate concretely why the architecture above exists. |
| **v2** | `prompts/v2_improved.md` | Facts-payload-only input, hard constraints against computing/inferring numbers, a fixed 6-section executive structure. | Replaced every V1 failure with a specific, checkable rule (see the mapping table in the prompt file itself). |
| **v3** (same file, revised in place) | `prompts/v2_improved.md` | Adds constraints against leaking payload field names into prose, self-translating month numbers, unverifiable superlatives, circular reasoning, and re-sorting/re-filtering payload lists itself. | Found by close-reading real generated output against the golden data — six separate corrections, two of which needed the *payload* changed (not just the prompt) once it became clear the model shouldn't be asked to sort or filter a list in prose either. |

## Where the AI helped and where it was wrong

Real observations, not predicted ones — every claim below is checked against an actual saved
output file.

**V1 (naive, no engine) — wrong in specific, checkable ways.** Growth percentages were wrong by
up to 5.9 points even against the model's own simple method; it had no way to know about the
Propolis phase-out (that fact only exists in `business_rules.yaml`, which V1 never sees) and
recommended restocking a product being discontinued; it measured Bioactive Blend growth against a
spurious December baseline; it conflated total revenue with revenue at risk; and it recommended
ranges ("1,500–2,000 units") instead of numbers. It also got real things right: every raw figure
it transcribed from the CSV was accurate, it correctly picked up MGO 1700+ 100g's 3-month cover
target straight from the CSV column, and its business instincts (flagging the 500g pack-size
format as underinvested) were sound even where its math wasn't. Full breakdown:
`prompts/ITERATION_LOG.md`.

**V2 (facts-payload, first real run) — a genuine caught hallucination.** Even with the payload
constraint in place, the first real generation invented two aggregate figures (a $98,025 headline
subtotal and a $660,000 excess-inventory total) that don't match anything in the payload —
`$722,788` was the real total. Post-generation validation caught both before either reached
`output/`; the failed transcript is preserved at `output/history/sop_briefing_2026-03_UNVERIFIED.md`
specifically as evidence the validator works, not just that it exists.

**V2→V3 corrections — grounding numbers isn't the same as getting the narrative right.** A
close read of V2's structurally-correct, numerically-clean output still found: raw payload field
names leaking into prose (`is_overstocked`, `phase_out_q2_2026_cover_above_30_days`); a month
mislabeled in one section and correctly labeled in another; an internally contradictory
superlative ("the strongest honey growth" followed two sentences later by a faster one); a
Stock-at-Risk table sorted inconsistently even after being told to sort it; and circular reasoning
citing a SKU's unit price as a justification when the adjacent revenue-at-risk figure already
priced that in. The circular-reasoning fix needed a *second attempt with the exact failing
sentence quoted back* before it actually stopped recurring on the same SKU — logged because "wrote
a rule" and "the rule worked" turned out to be different milestones. The sort-order fix needed a
second, architectural correction instead of a stronger sentence (see [Architecture](#architecture)
for that pattern generalized) — and even after that fix, a follow-up read of the regenerated
output found a related bug one level up: the Stock-at-Risk table listed a SKU as "at risk" of
stocking out while a later section explained that same SKU was overstocked and should have its
inbound PO reduced. The instruction had said how to *sort* the list but never said what should be
*on* it — fixed the same way, by filtering in `build_facts_payload()` rather than adding yet
another prompt sentence.

## Verification

Two independent layers, neither of which is "trust the model":

1. **Golden-value tests** (`pytest`, 67 tests, fully deterministic — no API calls). Every formula
   in `metrics.py` and every rule in `rules.py` is checked against hand-computed values from the
   real mock data (not copied from the code's own output — see the docstrings in `test_metrics.py`
   and `test_rules.py`), plus edge cases built from synthetic records: zero-baseline demand,
   `Order_Arrival_Months == 0` never treated as an immediate arrival, the growth cap engaging
   exactly on the two SKUs whose raw rate exceeds it, suppression lifting below its threshold,
   the tension flag's boundary conditions, and more.
2. **Post-generation numeric validation** (`src/narrative.py`). After Claude writes the briefing,
   `assert_narrative_numbers_verified` extracts every dollar figure and unit quantity from the
   prose via regex and checks each one against every numeric value present anywhere in the facts
   payload. Any figure that doesn't match raises `NarrativeValidationError`, naming the exact
   unverified claim, before the file is saved. This isn't a described intention — it caught a real
   hallucination during development (see above).

The validator itself had a real bug worth naming, not just fixing. The dollar-amount regex assumed
thousands-separator commas — `$40,308` parsed fine, but `$40308` (no comma) matched only its first
three digits, silently reading it as `$403` and discarding the rest. That's a **false negative**:
the validator would have let a wrong figure through without raising anything, rather than failing
loudly on a right one. A validator with a false negative is worse than no validator at all, because
it produces the same false confidence a missing check would — "validation passed" — while actually
having checked less than it claimed to. Caught before it reached production output, fixed, and
locked in with a regression test
(`test_extract_claimed_numbers_handles_dollar_amount_without_thousands_separator`).

Run everything:

```bash
pytest -v                     # all 67 tests
python -m src.main --generate  # real pipeline + real validation, end to end
```

## Tradeoffs and what I would do next with real data

This is a 12-SKU, 4-month mock dataset built to exercise specific business rules. A real
deployment would need to replace several placeholder assumptions with real inputs:

- **Actual supplier lead times and MOQs**, per SKU and per supplier, instead of the
  `Order_Arrival_Months`-inferred 2-month default, the qualitative-only 3-month override for MGO
  1700+ 100g, and the uniform 100-unit rounding — all reasonable stand-ins for a mock dataset and
  wrong assumptions to ship against real purchasing terms.
- **A cost basis for margin-weighted prioritization.** Every ranking here is revenue-at-risk,
  which treats a low-margin SKU and a high-margin SKU the same if their revenue is the same. With
  COGS data, the priority list should rank by *gross profit* at risk, not top-line revenue — a
  real S&OP team cares more about protecting margin than protecting a dollar figure that includes
  cost.
- **Seasonality.** Four months of data can't distinguish a durable growth trend from a seasonal
  effect — is the Bioactive Blend line's acceleration a real demand curve, or a January
  new-year's-resolution wellness spike that fades by spring? The current model has no way to tell
  the difference, and it should say so more explicitly than it currently does.
- **A promotional calendar.** A month-over-month spike or dip caused by a past discount or ad push
  gets read by this model as organic growth or decline. Real deployment needs promo-period flags
  so trend and growth calculations can exclude or adjust for known promotional activity instead of
  attributing it to underlying demand.
- **Safety stock sized to demand variance, not a fixed target.** `target_months_cover` is currently
  a flat number per SKU (2 or 3 months). A real implementation should size safety stock from the
  actual variance of historical demand relative to lead time (e.g., a service-level target times
  the standard deviation of demand during lead time) — a volatile, newly-launched line like
  Bioactive Blend needs a different buffer than a stable, mature SKU like MGO 263+ 250g, and a
  single flat target can't express that.
