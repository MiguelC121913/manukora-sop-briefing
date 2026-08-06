"""Tests for the SKU-level business rules layer.

Values against the real mock data were confirmed by running the pipeline
and cross-checking (see conversation/commit log for the dump), not by
copying compute_metrics output blindly.
"""

import inspect
from pathlib import Path

import pytest

import src.rules as rules_module
from src.loader import SalesRecord, load_business_rules, load_sales_data
from src.metrics import SKUMetrics, compute_all_metrics
from src.rules import RulesError, apply_business_rules

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_CSV = REPO_ROOT / "data" / "mock_sales.csv"
BUSINESS_RULES_YAML = REPO_ROOT / "config" / "business_rules.yaml"


@pytest.fixture(scope="module")
def business_rules():
    return load_business_rules(BUSINESS_RULES_YAML)


@pytest.fixture(scope="module")
def records(business_rules):
    return load_sales_data(DATA_CSV, business_rules=business_rules).records


@pytest.fixture(scope="module")
def metrics_results(records, business_rules):
    return compute_all_metrics(records, business_rules).results


@pytest.fixture(scope="module")
def rules_batch(records, metrics_results, business_rules):
    return apply_business_rules(records, metrics_results, business_rules)


def _rec(batch, sku):
    return next(r for r in batch.recommendations if r.sku == sku)


# --- Rule 1: Propolis phase-out suppression --------------------------------


def test_propolis_reorder_suppressed_with_exact_reason_string(rules_batch):
    r = _rec(rules_batch, "Propolis Tincture 30ml")
    assert r.metrics.reorder_quantity == 700  # raw math untouched
    assert r.final_reorder_quantity == 0      # suppressed
    assert r.suppression_reason == "phase_out_q2_2026_cover_above_30_days"


def test_propolis_stockout_month_still_reported_despite_suppression(rules_batch):
    r = _rec(rules_batch, "Propolis Tincture 30ml")
    assert r.metrics.projected_stockout_month == 2  # unchanged, still surfaced


def test_propolis_excluded_from_priority_reorder_list(rules_batch):
    skus_in_list = {entry["sku"] for entry in rules_batch.priority_reorder_list}
    assert "Propolis Tincture 30ml" not in skus_in_list


def test_phase_out_suppression_lifts_below_threshold(business_rules):
    """If a phase-out SKU's cover drops below the configured threshold, the
    raw reorder must NOT be suppressed — the business rule explicitly says
    "unless cover drops below 30 days."
    """
    from src.metrics import compute_metrics

    low_stock_record = SalesRecord(
        sku="Propolis Tincture 30ml",
        shopify_m1=76, shopify_m2=88, shopify_m3=96, shopify_m4=104,
        amazon_m1=44, amazon_m2=52, amazon_m3=56, amazon_m4=64,
        stock_on_hand=20,  # much lower than the real 230 -> cover well under 30 days
        units_on_order=0, order_arrival_months=0,
        target_months_cover=2, retail_price_usd=34.99, trend_start_month=1,
    )
    m = compute_metrics(low_stock_record, business_rules)
    assert m.current_cover_days < 30

    batch = apply_business_rules([low_stock_record], [m], business_rules)
    r = batch.recommendations[0]
    assert r.suppression_reason is None
    assert r.final_reorder_quantity == m.reorder_quantity
    assert r.final_reorder_quantity > 0


# --- Rule 2: MGO 1700+ uses config, never hardcoded values -----------------


def test_mgo_1700_policy_comes_from_config(metrics_results):
    m = next(x for x in metrics_results if x.sku == "Manuka Honey MGO 1700+ 100g")
    assert m.target_months_cover_policy == 3
    assert m.supplier_lead_time_months == 3


def test_mgo_1700_policy_changes_when_config_changes(records, business_rules):
    """Prove it's actually reading config at runtime, not hardcoded: mutate
    the override and confirm the output follows.
    """
    from src.metrics import compute_metrics

    mutated_rules = {
        **business_rules,
        "sku_overrides": {
            **business_rules["sku_overrides"],
            "Manuka Honey MGO 1700+ 100g": {
                **business_rules["sku_overrides"]["Manuka Honey MGO 1700+ 100g"],
                "target_months_cover": 5,
                "supplier_lead_time_months": 4,
            },
        },
    }
    record = next(r for r in records if r.sku == "Manuka Honey MGO 1700+ 100g")
    m = compute_metrics(record, mutated_rules)
    assert m.target_months_cover_policy == 5
    assert m.supplier_lead_time_months == 4


def test_rules_module_does_not_hardcode_mgo_1700_sku_name():
    """rules.py must apply the phase-out/tension rules generically off
    config fields, never by special-casing a SKU name/number in code.
    """
    source = inspect.getsource(rules_module)
    assert "1700" not in source


# --- Rule 3: Bioactive trend notes never reference M1 ----------------------


@pytest.mark.parametrize(
    "sku",
    [
        "Bioactive Blend Immunity 250g",
        "Bioactive Blend Energy 250g",
        "Bioactive Blend Recovery 250g",
    ],
)
def test_bioactive_skus_carry_m1_exclusion_note(rules_batch, sku):
    r = _rec(rules_batch, sku)
    assert r.metrics.trend_baseline_month == 2
    assert any("M1" in note and "Do not reference" in note for note in r.notes)


def test_non_bioactive_sku_has_no_m1_exclusion_note(rules_batch):
    r = _rec(rules_batch, "Manuka Honey MGO 100+ 250g")
    assert not any("Do not reference M1" in note for note in r.notes)


# --- Rule 4: demand-decline tension flag ------------------------------------


def test_no_tension_flag_on_real_data_this_month(rules_batch):
    """On this month's real mock data, the top-3-revenue SKUs (MGO 263+
    250g, 514+ 250g, 263+ 500g) are all steady/accelerating with positive
    channel MoM — so no tension flag fires. This is the correct outcome of
    the rule, not a bug: it proves the top-3 filter is actually being
    applied (MGO 100+ 250g is stalling with negative Amazon MoM, but ranks
    #7 by revenue, outside the top 3, and is correctly NOT flagged).
    """
    flagged = [r.sku for r in rules_batch.recommendations if r.tension_flag is not None]
    assert flagged == []

    mgo_100 = _rec(rules_batch, "Manuka Honey MGO 100+ 250g")
    assert mgo_100.metrics.trend == "stalling"
    assert mgo_100.tension_flag is None  # not top-3 by revenue


def _make_metrics(**overrides) -> SKUMetrics:
    defaults = dict(
        sku="Synthetic SKU",
        demand_m1=100, demand_m2=100, demand_m3=100, demand_m4=100,
        baseline_demand=100, projected_demand=100,
        growth_rate_raw=0.0, growth_rate=0.0, growth_rate_capped=False,
        trend_baseline_month=1,
        current_cover_months=2.0, cover_with_po_months=2.0, current_cover_days=60.0,
        demand_path=[100] * 12, projected_stockout_month=None,
        supplier_lead_time_months=2, target_months_cover_policy=2,
        demand_during_lead_time=200, target_buffer_at_arrival=200,
        available_within_lead_time=400, units_needed=0, reorder_quantity=0,
        revenue_opportunity_monthly=1000.0,
        is_overstocked=False, excess_units=0, excess_retail_value=0.0,
        mom_growth=0.05, trend="steady",
        shopify_mom_growth=0.05, amazon_mom_growth=0.05,
    )
    defaults.update(overrides)
    return SKUMetrics(**defaults)


def _make_record(**overrides) -> SalesRecord:
    defaults = dict(
        sku="Synthetic SKU",
        shopify_m1=50, shopify_m2=50, shopify_m3=50, shopify_m4=50,
        amazon_m1=50, amazon_m2=50, amazon_m3=50, amazon_m4=50,
        stock_on_hand=200, units_on_order=0,
        order_arrival_months=0, target_months_cover=2,
        retail_price_usd=10.0, trend_start_month=1,
    )
    defaults.update(overrides)
    return SalesRecord(**defaults)


def _minimal_rules() -> dict:
    return {
        "defaults": {
            "supplier_lead_time_months": 2,
            "target_months_cover": 2,
            "max_monthly_growth_rate": 0.12,
            "order_rounding_units": 100,
            "projection_horizon_months": 12,
        },
        "sku_overrides": {},
    }


def test_tension_flag_triggers_for_top3_revenue_stalling_sku():
    high_rev_stalling = _make_metrics(
        sku="Top Revenue Stalling", revenue_opportunity_monthly=100_000, trend="stalling",
        shopify_mom_growth=0.01, amazon_mom_growth=0.01, reorder_quantity=500,
    )
    other_top = [
        _make_metrics(sku=f"Filler {i}", revenue_opportunity_monthly=50_000 - i, reorder_quantity=100)
        for i in range(2)
    ]
    low_rev_stalling = _make_metrics(
        sku="Low Revenue Stalling", revenue_opportunity_monthly=500, trend="stalling",
        shopify_mom_growth=-0.05,
    )
    all_metrics = [high_rev_stalling, *other_top, low_rev_stalling]
    records = [_make_record(sku=m.sku) for m in all_metrics]
    rules = _minimal_rules()

    batch = apply_business_rules(records, all_metrics, rules)

    flagged = _rec(batch, "Top Revenue Stalling")
    assert flagged.tension_flag == "high_revenue_declining_demand"
    assert flagged.tension_supporting_figures["revenue_opportunity_monthly"] == 100_000
    assert flagged.tension_supporting_figures["trend"] == "stalling"

    # Not in top 3 by revenue -> not flagged even though also stalling
    not_flagged = _rec(batch, "Low Revenue Stalling")
    assert not_flagged.tension_flag is None

    # metrics.reorder_quantity untouched by the tension flag
    assert flagged.metrics.reorder_quantity == 500
    # ...but excluded from the actionable priority list
    assert "Top Revenue Stalling" not in {e["sku"] for e in batch.priority_reorder_list}


def test_tension_flag_triggers_on_negative_channel_mom_even_if_trend_not_stalling():
    m = _make_metrics(
        sku="Negative Channel", revenue_opportunity_monthly=90_000,
        trend="steady", shopify_mom_growth=-0.01, amazon_mom_growth=0.05,
    )
    fillers = [_make_metrics(sku=f"Filler {i}", revenue_opportunity_monthly=80_000 - i) for i in range(2)]
    all_metrics = [m, *fillers]
    records = [_make_record(sku=x.sku) for x in all_metrics]
    batch = apply_business_rules(records, all_metrics, _minimal_rules())

    assert _rec(batch, "Negative Channel").tension_flag == "high_revenue_declining_demand"


def test_defer_or_reduce_inbound_po_when_tension_and_overstocked_and_po_pending():
    m = _make_metrics(
        sku="Tension Overstocked", revenue_opportunity_monthly=90_000,
        trend="stalling", is_overstocked=True,
    )
    fillers = [_make_metrics(sku=f"Filler {i}", revenue_opportunity_monthly=80_000 - i) for i in range(2)]
    all_metrics = [m, *fillers]
    records = [
        _make_record(sku="Tension Overstocked", units_on_order=500),
        *[_make_record(sku=x.sku) for x in fillers],
    ]
    batch = apply_business_rules(records, all_metrics, _minimal_rules())

    r = _rec(batch, "Tension Overstocked")
    assert r.recommended_action == "defer_or_reduce_inbound_po"


def test_no_defer_recommendation_when_tension_but_not_overstocked():
    m = _make_metrics(
        sku="Tension Not Overstocked", revenue_opportunity_monthly=90_000,
        trend="stalling", is_overstocked=False,
    )
    fillers = [_make_metrics(sku=f"Filler {i}", revenue_opportunity_monthly=80_000 - i) for i in range(2)]
    all_metrics = [m, *fillers]
    records = [_make_record(sku=x.sku, units_on_order=500) for x in all_metrics]
    batch = apply_business_rules(records, all_metrics, _minimal_rules())

    r = _rec(batch, "Tension Not Overstocked")
    assert r.recommended_action is None


def test_no_defer_recommendation_when_tension_and_overstocked_but_no_po_pending():
    m = _make_metrics(
        sku="Tension No PO", revenue_opportunity_monthly=90_000,
        trend="stalling", is_overstocked=True,
    )
    fillers = [_make_metrics(sku=f"Filler {i}", revenue_opportunity_monthly=80_000 - i) for i in range(2)]
    all_metrics = [m, *fillers]
    records = [_make_record(sku=x.sku, units_on_order=0) for x in all_metrics]
    batch = apply_business_rules(records, all_metrics, _minimal_rules())

    r = _rec(batch, "Tension No PO")
    assert r.recommended_action is None


# --- Prioritization: revenue at risk, not revenue in total -----------------


def test_priority_reorder_list_matches_expected_order_on_real_data(rules_batch):
    skus_in_order = [entry["sku"] for entry in rules_batch.priority_reorder_list]
    assert skus_in_order == [
        "Manuka Honey MGO 263+ 500g",
        "Manuka Honey MGO 514+ 500g",
        "Manuka Honey MGO 850+ 500g",
        "Bioactive Blend Immunity 250g",
        "Manuka Honey MGO 1700+ 100g",
        "Bioactive Blend Energy 250g",
        "Bioactive Blend Recovery 250g",
    ]


def test_priority_reorder_list_is_sorted_by_revenue_at_risk_descending(rules_batch):
    revenues = [entry["revenue_at_risk_monthly"] for entry in rules_batch.priority_reorder_list]
    assert revenues == sorted(revenues, reverse=True)


def test_high_revenue_well_stocked_sku_excluded_from_priority_list(rules_batch):
    """MGO 263+ 250g is the single highest-revenue SKU in the entire
    dataset ($59,763/mo) but sits on ~4.5 months of cover against a 2-month
    target — reorder_quantity is 0, so it has no revenue at risk and must
    not appear, despite its revenue being larger than everything in the
    list.
    """
    skus_in_list = {entry["sku"] for entry in rules_batch.priority_reorder_list}
    assert "Manuka Honey MGO 263+ 250g" not in skus_in_list

    r = _rec(rules_batch, "Manuka Honey MGO 263+ 250g")
    assert r.final_reorder_quantity == 0
    assert r.metrics.revenue_opportunity_monthly > max(
        entry["revenue_at_risk_monthly"] for entry in rules_batch.priority_reorder_list
    )


def test_priority_reorder_list_entries_have_expected_shape(rules_batch):
    entry = rules_batch.priority_reorder_list[0]
    assert set(entry.keys()) == {
        "sku", "reorder_quantity", "revenue_at_risk_monthly",
        "current_cover_days", "projected_stockout_month",
    }


# --- RulesError -------------------------------------------------------------


def test_rules_error_when_metrics_and_records_out_of_sync():
    m = _make_metrics(sku="Ghost SKU")
    with pytest.raises(RulesError, match="Ghost SKU"):
        apply_business_rules([], [m], _minimal_rules())
