# Manukora S&OP Executive Briefing — March 2026

---

## 1. The Decision

Seven SKUs need reorders this month, with **$181,224** in monthly revenue at risk of stockout — against a portfolio generating **$364,803** in monthly revenue opportunity. Three high-grade 500g honeys (MGO 263+, 514+, 850+) and two Bioactive Blends (Energy, Recovery) will exhaust stock within roughly two months; act now or lose shelf presence on your fastest-growing lines. Four SKUs are overstocked, carrying **$722,788** in excess retail value that is tying up capital — that imbalance across the portfolio is as urgent as the stockouts. Approve the seven reorders in Section 4 and review the overstock positions in Section 5 before committing further inbound POs on those lines.

---

## 2. What Moved This Month

**Accelerating lines:** The MGO 500g sizes (263+, 514+, 850+) are all growing faster than their 250g counterparts, with MGO 514+ 500g posting the strongest honey growth at 11.4% month-over-month. MGO 1700+ 100g is the portfolio's standout performer at 13.6% MoM growth. All three Bioactive Blends are accelerating sharply (11–14% MoM from their February baseline onward — see caveats on December exclusion).

**Steady growth:** MGO 263+ 250g is growing consistently at 5.0% MoM across both channels, with Shopify (6.7%) running notably ahead of Amazon (2.5%) — a signal worth watching as it may reflect channel-specific promotions or listing quality differences. MGO 850+ 250g is similar: steady at 7.2% MoM overall, with Shopify (8.1%) again outpacing Amazon (5.9%).

**Channel divergence to flag:** MGO 100+ 250g is the one SKU where channels are moving in opposite directions — Shopify is up 3.9% MoM while Amazon is *down* 4.0%. The net result is a near-flat 0.8% blended growth rate, and the trend is classified as stalling. This divergence is a real signal: something is working on Shopify that isn't on Amazon (or vice versa), and it deserves a channel-level diagnosis before the next cycle.

**Propolis Tincture 30ml** is growing at 10.5% MoM (Amazon notably stronger at 14.3% vs. Shopify's 8.3%), but this is a managed phase-out — demand momentum does not change that decision.

---

## 3. Stock at Risk

| SKU | Projected Stockout | Monthly Revenue Exposed |
|---|---|---|
| Manuka Honey MGO 514+ 500g | ~2 months | $34,316 |
| Manuka Honey MGO 850+ 500g | ~2 months | $29,477 |
| Bioactive Blend Energy 250g | ~2 months | $17,396 |
| Bioactive Blend Recovery 250g | ~2 months | $16,316 |
| Propolis Tincture 30ml *(suppressed — see Section 5)* | ~2 months | $6,578 |
| Manuka Honey MGO 263+ 500g | ~3 months | $40,308 |
| Bioactive Blend Immunity 250g | ~4 months | $23,554 |
| Manuka Honey MGO 1700+ 100g | ~4 months | $19,857 |

Four SKUs face stockout within approximately two months — inside or at the edge of standard lead time. The Propolis Tincture is included for completeness; its reorder is suppressed by design, not oversight (see Section 5).

---

## 4. Reorder Recommendations

*Ranked by revenue at risk, descending. All quantities from `priority_reorder_list`.*

| SKU | Reorder Qty | Revenue at Risk/Mo | Rationale |
|---|---|---|---|
| Manuka Honey MGO 263+ 500g | 1,500 units | $40,308 | Stockout in ~3 months on an accelerating line (8.9% MoM); no units on order and 2-month lead time leaves zero buffer — order immediately. |
| Manuka Honey MGO 514+ 500g | 1,200 units | $34,316 | Stockout in ~2 months with only 59.7 days of cover and no inbound stock; at 11.4% MoM growth this SKU is gaining velocity exactly when supply is tightest. |
| Manuka Honey MGO 850+ 500g | 800 units | $29,477 | Stockout in ~2 months, 54.1 days cover, no units on order; at $109.99 retail this is the highest-price honey in the range and a stockout would disproportionately damage revenue per unit lost. |
| Bioactive Blend Immunity 250g | 800 units | $23,554 | Stockout in ~4 months but demand is accelerating at 11.9% MoM; a PO of 800 units with 2-month lead time is needed now to land stock before the cover window closes. |
| Manuka Honey MGO 1700+ 100g | 1,200 units | $19,857 | This SKU carries a 3-month supplier lead time (vs. the 2-month default), making its ~4-month stockout horizon effectively tight — order now or the reorder arrives too late. |
| Bioactive Blend Energy 250g | 1,300 units | $17,396 | Stockout in ~2 months with only 57.2 days cover and no inbound stock; growth is capped at 12% in the model (see Section 6) — even at that conservative rate, demand will outpace current supply within the lead-time window. |
| Bioactive Blend Recovery 250g | 1,300 units | $16,316 | Stockout in ~2 months, 50.3 days cover, no inbound stock; like Energy, growth is capped at 12% — the underlying raw rate was higher, so actual risk may exceed the model's projection. |

---

## 5. Judgment Calls

**Propolis Tincture 30ml — Controlled Phase-Out, Not a Stockout:**
This SKU's reorder has been deliberately suppressed under the plan `phase_out_q2_2026_cover_above_30_days`. With 41.1 days of cover remaining and a projected stockout in approximately two months, this is a managed exit — stock runs down and the line is discontinued by Q2 2026. No reorder should be placed. However, the operational implication is real: channel communications (Amazon listing wind-down, Shopify removal) need to be scheduled now so the stockout is a planned delisting, not a surprise out-of-stock. The SKU is generating $6,578/month in revenue at list price — that revenue disappears at stockout and should not be back-filled.

**MGO 100+ 250g — Overstocked with Stalling Demand:**
This SKU is overstocked (`is_overstocked: true`) and its trend is classified as stalling (0.8% blended MoM growth). It carries 6.2 months of cover on hand, plus an additional PO already in transit that will extend cover to 8.14 months — well beyond the 2-month policy target. The $156,387 in excess retail value sitting in this inventory is not growing into demand; it's simply aging stock on an uncertain trajectory. No reorder is needed, and the channel divergence flagged in Section 2 (Shopify +3.9% vs. Amazon -4.0%) should be investigated before any future replenishment is considered.

**MGO 263+: Pack-Size Imbalance — 250g Overstocked, 500g Critical:**
Within the same MGO grade, the two pack sizes are in completely opposite positions. The 250g has 4.55 months of cover on hand (6.55 months with PO), is flagged overstocked with $247,869 in excess retail value, and requires no reorder. The 500g has only 2.49 months of cover, no units on order, a stockout projected in approximately three months, and is the top reorder priority in this cycle at 1,500 units. Capital and supply allocation have tilted heavily toward the 250g while the 500g — which is accelerating faster (8.9% MoM vs. 5.0%) — is running out. This is a sourcing and allocation decision: future POs for MGO 263+ should rebalance toward the 500g format until cover levels converge.

---

## 6. Assumptions and Caveats

- **Lead times:** Default supplier lead time is **2 months**. One exception: MGO 1700+ 100g carries a **3-month** supplier lead time, which is why its ~4-month stockout horizon is treated as urgent rather than comfortable.
- **Growth cap:** All demand projections cap monthly growth at **12.0%** per business rules. Two SKUs hit this ceiling: Bioactive Blend Energy 250g (raw rate 12.2%, capped to 12.0%) and Bioactive Blend Recovery 250g (raw rate 13.2%, capped to 12.0%). Reorder quantities for both are sized to the capped rate — actual demand may outpace these projections.
- **Revenue figures use full list price** (`retail_price_usd`) — not net of promotions, discounts, or wholesale margin. Revenue at risk and opportunity figures will be higher than realized net revenue.
- **Data quality — Bioactive Blend December exclusion:** December 2025 sales data exists for Bioactive Blend Immunity, Energy, and Recovery, but these SKUs launched mid-window and their December figures are inconsistent with the trend baseline. Per business rules, December has been excluded from trend and growth calculations for all three — trends are computed from January 2026 onward only. This is a known data inconsistency in the source, not a modeling error.