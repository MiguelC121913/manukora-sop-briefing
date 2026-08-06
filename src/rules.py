"""SKU-level business rules layered on top of metrics.py's raw calculations.

metrics.py computes what the numbers literally say. This module applies the
business judgment on top of them — suppressing a reorder that would
otherwise cost money on a product being phased out, flagging tension
between high revenue and declining demand, and building the ranked
"what to actually reorder" list an executive needs.

Every override records a machine-readable reason (`suppression_reason`,
`tension_flag`) rather than silently changing a number, so the narrative
layer explains a decision instead of inventing one, and a human reviewer
can trace every deviation from the raw metrics.py output back to a rule.

Design decisions worth being explicit about:

- Rules are keyed off the *presence of config fields* in business_rules.yaml
  (e.g. `phase_out_quarter`), never off a SKU's literal name. Nothing in
  this module special-cases a specific SKU's name or number as a string to
  match on — target cover / lead time for any SKU (premium or otherwise)
  come entirely from business_rules.yaml's defaults/overrides. test_rules.py
  enforces this directly by scanning this file's source for SKU-specific
  literals.

- "Do not auto-recommend a reorder for it" (the demand-decline tension
  rule) is implemented as *exclusion from the priority reorder list*, not
  as zeroing metrics.reorder_quantity. Only the phase-out rule forces the
  underlying number to 0 — the spec is explicit about that one. For a
  tension-flagged SKU, the raw reorder_quantity from metrics.py is left
  untouched (it's still true, deterministic math) but the SKU is held out
  of the automated recommendation surface pending human review. This is a
  judgment call rather than something the brief mandates numerically;
  documented here and in the README.

- **Tension flag criterion, revised.** Originally gated to the top 3 SKUs
  by `revenue_opportunity_monthly` (the initial spec's own choice). That
  threshold proved too narrow in practice: it excluded MGO 100+ 250g, the
  dataset's clearest tension case (overstocked, stalling demand, and a PO
  still in transit) — the SKU ranks #7 by revenue, not top-3. The
  criterion is now: `is_overstocked` AND a declining-demand signal
  (`trend == "stalling"` OR either channel's MoM is negative) — no revenue
  ranking at all. Overstock is the real gate: capital tied up in excess
  inventory *combined with* a stalling/declining signal is the tension,
  independent of how large the SKU's revenue line is. The `tension_flag`
  value string (`"high_revenue_declining_demand"`) is left as-is for
  continuity with the original spec even though revenue is no longer the
  gating condition — see `prompts/ITERATION_LOG.md`'s v2→v3 note for the
  full before/after.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.loader import SalesRecord
from src.metrics import SKUMetrics


class RulesError(Exception):
    """Raised when the rules layer can't reconcile its inputs (e.g. a
    metrics result with no matching sales record) — fail loudly rather than
    silently skip a SKU an executive expects to see.
    """


@dataclass(frozen=True)
class SKURecommendation:
    sku: str
    metrics: SKUMetrics  # untouched raw calculation, always available for audit

    final_reorder_quantity: int
    suppression_reason: str | None

    tension_flag: str | None
    tension_supporting_figures: dict[str, Any] | None
    recommended_action: str | None  # e.g. "defer_or_reduce_inbound_po"

    notes: list[str] = field(default_factory=list)


@dataclass
class RulesBatch:
    recommendations: list[SKURecommendation]
    priority_reorder_list: list[dict[str, Any]]
    warnings: list[str] = field(default_factory=list)


def _parse_quarter(quarter_str: str) -> tuple[str, str]:
    """'2026-Q2' -> ('2026', 'q2'). Raises RulesError on unexpected format."""
    parts = quarter_str.split("-")
    if len(parts) != 2 or not parts[1].lower().startswith("q"):
        raise RulesError(f"Unrecognized phase_out_quarter format: '{quarter_str}' (expected 'YYYY-Qn').")
    year, quarter = parts
    return year, quarter.lower()


def _apply_phase_out_suppression(
    metrics: SKUMetrics, business_rules: dict[str, Any]
) -> tuple[int, str | None]:
    """Generic phase-out reorder suppression: any SKU whose business_rules.yaml
    override defines both `phase_out_quarter` and
    `reorder_only_if_cover_days_below` gets its reorder suppressed to 0 once
    cover is at or above that threshold — a controlled wind-down instead of
    a routine restock. Below the threshold, the raw reorder stands: a phase-
    out SKU that's about to hard-stockout still gets reordered.
    """
    override = (business_rules.get("sku_overrides", {}) or {}).get(metrics.sku, {}) or {}
    phase_out_quarter = override.get("phase_out_quarter")
    threshold_days = override.get("reorder_only_if_cover_days_below")

    if phase_out_quarter is None or threshold_days is None:
        return metrics.reorder_quantity, None

    if metrics.current_cover_days >= threshold_days:
        year, quarter = _parse_quarter(phase_out_quarter)
        reason = f"phase_out_{quarter}_{year}_cover_above_{int(threshold_days)}_days"
        return 0, reason

    return metrics.reorder_quantity, None


def _compute_tension_flags(results: list[SKUMetrics]) -> dict[str, dict[str, Any]]:
    """Any SKU that is both overstocked and showing a declining-demand
    signal (stalling trend, or either channel's MoM negative) gets flagged
    — regardless of revenue rank. See the module docstring's "Tension flag
    criterion, revised" note for why this replaced the original top-3-by-
    revenue gate.
    """
    flags: dict[str, dict[str, Any]] = {}

    for m in results:
        channel_negative = (m.shopify_mom_growth is not None and m.shopify_mom_growth < 0) or (
            m.amazon_mom_growth is not None and m.amazon_mom_growth < 0
        )
        declining_signal = m.trend == "stalling" or channel_negative
        if declining_signal and m.is_overstocked:
            flags[m.sku] = {
                "tension_flag": "high_revenue_declining_demand",
                "supporting_figures": {
                    "revenue_opportunity_monthly": m.revenue_opportunity_monthly,
                    "trend": m.trend,
                    "mom_growth": m.mom_growth,
                    "shopify_mom_growth": m.shopify_mom_growth,
                    "amazon_mom_growth": m.amazon_mom_growth,
                    "is_overstocked": m.is_overstocked,
                },
            }

    return flags


def _build_priority_reorder_list(recommendations: list[SKURecommendation]) -> list[dict[str, Any]]:
    """Revenue AT RISK, not revenue in total: only SKUs with a positive
    final reorder quantity (current inventory is insufficient against
    policy) appear here, ranked by their monthly revenue opportunity. A
    high-revenue SKU sitting on months of cover has nothing at risk and is
    correctly absent, regardless of how large its revenue number is.
    Tension-flagged SKUs are held out too — see module docstring.
    """
    eligible = [r for r in recommendations if r.final_reorder_quantity > 0 and r.tension_flag is None]
    ranked = sorted(eligible, key=lambda r: r.metrics.revenue_opportunity_monthly, reverse=True)
    return [
        {
            "sku": r.sku,
            "reorder_quantity": r.final_reorder_quantity,
            "revenue_at_risk_monthly": round(r.metrics.revenue_opportunity_monthly, 2),
            "current_cover_days": round(r.metrics.current_cover_days, 1),
            "projected_stockout_month": r.metrics.projected_stockout_month,
        }
        for r in ranked
    ]


def apply_business_rules(
    records: list[SalesRecord],
    metrics_results: list[SKUMetrics],
    business_rules: dict[str, Any],
) -> RulesBatch:
    """Layer SKU-level business rules on top of raw metrics.py output.

    Returns one SKURecommendation per input SKU (order follows
    metrics_results) plus a revenue-at-risk-ranked priority_reorder_list and
    a flat list of human-readable warnings for anything overridden.
    """
    records_by_sku = {r.sku: r for r in records}
    tension_flags = _compute_tension_flags(metrics_results)

    recommendations: list[SKURecommendation] = []
    warnings: list[str] = []

    for m in metrics_results:
        record = records_by_sku.get(m.sku)
        if record is None:
            raise RulesError(f"No sales record found for SKU '{m.sku}' — metrics/records out of sync.")

        final_reorder_quantity, suppression_reason = _apply_phase_out_suppression(m, business_rules)
        if suppression_reason:
            stockout_note = (
                f" Stockout still projected in month {m.projected_stockout_month} — "
                f"manage as a controlled exit (channel comms, no restock), not a surprise stockout."
                if m.projected_stockout_month is not None
                else " No stockout currently projected within the horizon."
            )
            warnings.append(
                f"Reorder suppressed for {m.sku}: {suppression_reason} "
                f"(current cover {m.current_cover_days:.0f} days)." + stockout_note
            )

        tension_info = tension_flags.get(m.sku)
        tension_flag = tension_info["tension_flag"] if tension_info else None
        tension_figures = tension_info["supporting_figures"] if tension_info else None

        recommended_action: str | None = None
        notes: list[str] = []

        if tension_flag:
            notes.append(
                f"{m.sku} is overstocked (${m.revenue_opportunity_monthly:,.0f}/mo revenue "
                f"opportunity) and showing a declining-demand signal (trend={m.trend}, "
                f"Shopify MoM={_fmt_pct(m.shopify_mom_growth)}, "
                f"Amazon MoM={_fmt_pct(m.amazon_mom_growth)}). Held out of the automated "
                f"reorder recommendation pending review, even though metrics.py computed "
                f"a raw reorder_quantity of {m.reorder_quantity} units."
            )
            warnings.append(
                f"Tension flag: {m.sku} — overstocked with a declining demand signal. "
                f"Not auto-recommended for reorder."
            )
            if m.is_overstocked and record.units_on_order > 0:
                recommended_action = "defer_or_reduce_inbound_po"
                notes.append(
                    f"{m.sku} is already overstocked with {record.units_on_order} units inbound — "
                    f"recommend deferring or reducing that inbound PO rather than reordering further."
                )

        if m.trend_baseline_month == 2:
            notes.append(
                f"{m.sku}: trend and growth figures are computed from M2 onward only "
                f"(M1 excluded per business_rules.yaml trend_baseline_month override — "
                f"see loader data_quality_warnings). Do not reference M1 when describing this SKU's trend."
            )

        recommendations.append(
            SKURecommendation(
                sku=m.sku,
                metrics=m,
                final_reorder_quantity=final_reorder_quantity,
                suppression_reason=suppression_reason,
                tension_flag=tension_flag,
                tension_supporting_figures=tension_figures,
                recommended_action=recommended_action,
                notes=notes,
            )
        )

    priority_reorder_list = _build_priority_reorder_list(recommendations)

    return RulesBatch(
        recommendations=recommendations,
        priority_reorder_list=priority_reorder_list,
        warnings=warnings,
    )


def _fmt_pct(value: float | None) -> str:
    return f"{value:+.1%}" if value is not None else "n/a"
