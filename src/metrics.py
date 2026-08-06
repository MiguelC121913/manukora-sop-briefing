"""Deterministic S&OP metrics engine.

Every number an executive sees originates here, not from the LLM. This
module takes validated SalesRecord objects (from loader.py) plus the parsed
business_rules.yaml and computes growth, cover, stockout risk, and
lead-time-aware reorder quantities using fixed formulas — no estimation, no
LLM involvement.

Design note on where each input comes from:
- Per-SKU *data* (Order_Arrival_Months for an already-placed PO, current
  Stock_On_Hand, etc.) comes from the CSV via SalesRecord.
- Per-SKU *policy* (target_months_cover, supplier_lead_time_months) comes
  from business_rules.yaml (sku_overrides falling back to defaults), not
  from the CSV, so the S&OP team can change policy by editing YAML without
  touching code or the sales data file. Order_Arrival_Months and
  supplier_lead_time_months are deliberately different concepts: the former
  is when a PO already in flight lands; the latter is how long a *new*
  order placed today would take, which is what the reorder trigger cares
  about.

This module does NOT apply SKU-specific narrative overrides (e.g. "suppress
Propolis reorders unless cover drops below 30 days" ahead of its phase-out).
Those live in rules.py and are applied on top of these raw, literal
calculations — keeping "what the math says" separate from "what the
business wants to do about it."
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from src.loader import SalesRecord


class MetricsError(Exception):
    """Raised when a metric cannot be computed from the given data/config
    (e.g. division by zero baseline demand). We fail loudly rather than
    emit inf/NaN/garbage into a payload an executive might read.
    """


@dataclass(frozen=True)
class SKUMetrics:
    sku: str

    # Demand
    demand_m1: int
    demand_m2: int
    demand_m3: int
    demand_m4: int
    baseline_demand: int          # = demand_m4
    projected_demand: int         # demand_m4 * (1+g), rounded

    # Growth
    growth_rate_raw: float
    growth_rate: float            # capped
    growth_rate_capped: bool
    trend_baseline_month: int     # 1 or 2, per business_rules override

    # Cover
    current_cover_months: float
    cover_with_po_months: float
    current_cover_days: float

    # 12-month (or configured horizon) demand path and stockout risk
    demand_path: list[int]        # demand_path[i] = projected demand for month i+1
    projected_stockout_month: int | None

    # Reorder (lead-time aware)
    supplier_lead_time_months: int
    target_months_cover_policy: int
    demand_during_lead_time: int
    target_buffer_at_arrival: int
    available_within_lead_time: int
    units_needed: int
    reorder_quantity: int

    # Revenue
    revenue_opportunity_monthly: float

    # Excess inventory
    is_overstocked: bool
    excess_units: int
    excess_retail_value: float

    # Trend classification
    mom_growth: float
    trend: str
    shopify_mom_growth: float | None
    amazon_mom_growth: float | None


@dataclass
class MetricsBatch:
    results: list[SKUMetrics]
    warnings: list[str] = field(default_factory=list)


def _resolve_sku_policy(sku: str, business_rules: dict[str, Any]) -> tuple[int, int]:
    """Resolve (target_months_cover, supplier_lead_time_months) for a SKU
    from business_rules.yaml: sku_overrides take precedence over defaults.
    """
    defaults = business_rules.get("defaults", {}) or {}
    override = (business_rules.get("sku_overrides", {}) or {}).get(sku, {}) or {}

    target_months_cover = override.get("target_months_cover", defaults.get("target_months_cover"))
    lead_time = override.get("supplier_lead_time_months", defaults.get("supplier_lead_time_months"))

    if target_months_cover is None or lead_time is None:
        raise MetricsError(
            f"{sku}: business_rules.yaml has no target_months_cover/"
            f"supplier_lead_time_months in defaults (and no SKU override)."
        )
    return int(target_months_cover), int(lead_time)


def _safe_mom(current: int, previous: int) -> float | None:
    """Month-over-month growth, or None if the prior month was zero (can't
    compute a meaningful ratio — not a data error worth crashing on, since a
    single channel legitimately being zero one month is plausible).
    """
    if previous == 0:
        return None
    return current / previous - 1


def _classify_trend(mom_growth: float) -> str:
    if mom_growth < 0.02:
        return "stalling"
    elif mom_growth < 0.08:
        return "steady"
    else:
        return "accelerating"


def compute_metrics(record: SalesRecord, business_rules: dict[str, Any]) -> SKUMetrics:
    """Compute all deterministic metrics for a single SKU. Raises
    MetricsError if the data/config makes a formula undefined (e.g. a zero
    baseline demand) rather than returning a silently wrong number.
    """
    demand = {
        1: record.shopify_m1 + record.amazon_m1,
        2: record.shopify_m2 + record.amazon_m2,
        3: record.shopify_m3 + record.amazon_m3,
        4: record.shopify_m4 + record.amazon_m4,
    }

    # --- Growth rate --------------------------------------------------
    baseline_month = record.trend_start_month  # set by loader.py from
    # business_rules.yaml's trend_baseline_month override (1 or 2)
    n = 4 - baseline_month
    if demand[baseline_month] <= 0:
        raise MetricsError(
            f"{record.sku}: cannot compute growth rate — baseline month "
            f"M{baseline_month} demand is {demand[baseline_month]}."
        )

    g_raw = (demand[4] / demand[baseline_month]) ** (1 / n) - 1
    max_growth = business_rules.get("defaults", {}).get("max_monthly_growth_rate")
    if max_growth is None:
        raise MetricsError("business_rules.yaml defaults.max_monthly_growth_rate is not set.")
    g = min(g_raw, max_growth)
    growth_rate_capped = g_raw > max_growth

    baseline_demand = demand[4]
    projected_demand = round(demand[4] * (1 + g))

    # --- Cover ----------------------------------------------------------
    current_cover_months = record.stock_on_hand / baseline_demand
    cover_with_po_months = (record.stock_on_hand + record.units_on_order) / baseline_demand
    current_cover_days = current_cover_months * 30

    # --- Demand path and stockout projection ----------------------------
    horizon = business_rules.get("defaults", {}).get("projection_horizon_months", 12)
    demand_path = [round(demand[4] * (1 + g) ** k) for k in range(1, horizon + 1)]

    stock = record.stock_on_hand
    projected_stockout_month: int | None = None
    for k in range(1, horizon + 1):
        if record.units_on_order > 0 and record.order_arrival_months == k:
            stock += record.units_on_order
        stock -= demand_path[k - 1]
        if stock < 0:
            projected_stockout_month = k
            break

    # --- Reorder quantity (lead-time aware) ------------------------------
    target_months_cover, lead_time = _resolve_sku_policy(record.sku, business_rules)
    if lead_time > horizon:
        raise MetricsError(
            f"{record.sku}: supplier_lead_time_months ({lead_time}) exceeds "
            f"projection_horizon_months ({horizon}); cannot compute reorder quantity."
        )
    if lead_time < 1:
        raise MetricsError(f"{record.sku}: supplier_lead_time_months must be >= 1, got {lead_time}.")

    demand_during_lead_time = sum(demand_path[0:lead_time])
    d_at_arrival = demand_path[lead_time - 1]
    target_buffer_at_arrival = round(target_months_cover * d_at_arrival * (1 + g))
    available_within_lead_time = record.stock_on_hand + (
        record.units_on_order if 0 < record.order_arrival_months <= lead_time else 0
    )
    units_needed = demand_during_lead_time + target_buffer_at_arrival - available_within_lead_time
    order_rounding = business_rules.get("defaults", {}).get("order_rounding_units", 100)
    reorder_quantity = math.ceil(units_needed / order_rounding) * order_rounding if units_needed > 0 else 0

    # --- Revenue opportunity ---------------------------------------------
    revenue_opportunity_monthly = record.retail_price_usd * projected_demand

    # --- Excess inventory --------------------------------------------------
    is_overstocked = cover_with_po_months > (target_months_cover * 2)
    excess_units = max(
        0, (record.stock_on_hand + record.units_on_order) - target_months_cover * projected_demand
    )
    excess_retail_value = excess_units * record.retail_price_usd

    # --- Trend classification ---------------------------------------------
    if demand[3] <= 0:
        raise MetricsError(f"{record.sku}: cannot compute MoM growth — M3 demand is {demand[3]}.")
    mom_growth = demand[4] / demand[3] - 1
    trend = _classify_trend(mom_growth)
    shopify_mom_growth = _safe_mom(record.shopify_m4, record.shopify_m3)
    amazon_mom_growth = _safe_mom(record.amazon_m4, record.amazon_m3)

    return SKUMetrics(
        sku=record.sku,
        demand_m1=demand[1],
        demand_m2=demand[2],
        demand_m3=demand[3],
        demand_m4=demand[4],
        baseline_demand=baseline_demand,
        projected_demand=projected_demand,
        growth_rate_raw=g_raw,
        growth_rate=g,
        growth_rate_capped=growth_rate_capped,
        trend_baseline_month=baseline_month,
        current_cover_months=current_cover_months,
        cover_with_po_months=cover_with_po_months,
        current_cover_days=current_cover_days,
        demand_path=demand_path,
        projected_stockout_month=projected_stockout_month,
        supplier_lead_time_months=lead_time,
        target_months_cover_policy=target_months_cover,
        demand_during_lead_time=demand_during_lead_time,
        target_buffer_at_arrival=target_buffer_at_arrival,
        available_within_lead_time=available_within_lead_time,
        units_needed=units_needed,
        reorder_quantity=reorder_quantity,
        revenue_opportunity_monthly=revenue_opportunity_monthly,
        is_overstocked=is_overstocked,
        excess_units=excess_units,
        excess_retail_value=excess_retail_value,
        mom_growth=mom_growth,
        trend=trend,
        shopify_mom_growth=shopify_mom_growth,
        amazon_mom_growth=amazon_mom_growth,
    )


def compute_all_metrics(
    records: list[SalesRecord], business_rules: dict[str, Any]
) -> MetricsBatch:
    """Compute metrics for every SKU. Collects a warning for each SKU whose
    raw growth rate exceeded the configured cap, so the narrative layer
    (and a human reviewer) can see it was capped rather than discovering a
    silently altered number.
    """
    results: list[SKUMetrics] = []
    warnings: list[str] = []
    max_growth = business_rules.get("defaults", {}).get("max_monthly_growth_rate")

    for record in records:
        result = compute_metrics(record, business_rules)
        results.append(result)
        if result.growth_rate_capped:
            warnings.append(
                f"Growth rate capped: {result.sku} — raw compound monthly growth "
                f"{result.growth_rate_raw:.1%} exceeds the {max_growth:.0%} cap "
                f"(business_rules.yaml: max_monthly_growth_rate). Capped to "
                f"{result.growth_rate:.1%} to avoid unrealistic 12-month extrapolation."
            )

    return MetricsBatch(results=results, warnings=warnings)
