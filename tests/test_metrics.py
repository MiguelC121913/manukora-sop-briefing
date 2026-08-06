"""Tests for the deterministic metrics engine.

Expected values below were independently hand-computed from the formulas in
PHASE 2's spec (not copied from the implementation's output) and then
cross-checked against src/metrics.py's actual output before being pinned
here — see the commit message / conversation log for the by-hand math.
"""

from pathlib import Path

import pytest

from src.loader import SalesRecord, load_business_rules, load_sales_data
from src.metrics import MetricsError, compute_all_metrics, compute_metrics

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_CSV = REPO_ROOT / "data" / "mock_sales.csv"
BUSINESS_RULES_YAML = REPO_ROOT / "config" / "business_rules.yaml"


@pytest.fixture(scope="module")
def business_rules():
    return load_business_rules(BUSINESS_RULES_YAML)


@pytest.fixture(scope="module")
def records(business_rules):
    return load_sales_data(DATA_CSV, business_rules=business_rules).records


def _get(records, sku):
    return next(r for r in records if r.sku == sku)


# --- Hand-verified full-record checks --------------------------------------


def test_mgo_100_250g_well_stocked_no_reorder(records, business_rules):
    """Heavily overstocked SKU (cover ~6.2mo vs 2mo target): growth is
    modest and uncapped, reorder should be suppressed by the math itself
    (units_needed negative), not by any override.
    """
    record = _get(records, "Manuka Honey MGO 100+ 250g")
    m = compute_metrics(record, business_rules)

    assert m.demand_m4 == 1032  # 644 + 388
    assert m.baseline_demand == 1032
    assert m.growth_rate_raw == pytest.approx(0.037535, abs=1e-5)
    assert m.growth_rate == pytest.approx(m.growth_rate_raw)  # not capped
    assert not m.growth_rate_capped
    assert m.projected_demand == 1071

    assert m.current_cover_months == pytest.approx(6400 / 1032)
    assert m.cover_with_po_months == pytest.approx(8400 / 1032)

    assert m.demand_during_lead_time == 2182  # d1(1071) + d2(1111)
    assert m.units_needed < 0
    assert m.reorder_quantity == 0
    assert m.is_overstocked is True
    assert m.excess_units == 6258

    assert m.trend == "stalling"  # mom_growth 0.78% < 2%


def test_propolis_undersupplied_reorders_despite_no_override_applied(records, business_rules):
    """metrics.py is the raw math layer — it must NOT know about the
    Propolis phase-out / "suppress reorder unless cover < 30 days" rule.
    That override lives in rules.py (later phase) and is applied on top of
    this. Here, current_cover_days (~41) is above the 30-day threshold, so
    a human reading raw metrics.py output would see a reorder recommended;
    rules.py is what's responsible for suppressing it.
    """
    record = _get(records, "Propolis Tincture 30ml")
    m = compute_metrics(record, business_rules)

    assert m.demand_m4 == 168  # 104 + 64
    assert m.growth_rate_raw == pytest.approx(0.118689, abs=1e-5)
    assert not m.growth_rate_capped  # 11.87% is under the 12% cap
    assert m.current_cover_days == pytest.approx(41.07, abs=0.01)
    assert m.projected_stockout_month == 2
    assert m.reorder_quantity == 700
    assert m.units_needed == 638


def test_bioactive_energy_growth_rate_is_capped(records, business_rules):
    """Real data case where the cap actually engages: raw compound growth
    12.24% > 12% cap.
    """
    record = _get(records, "Bioactive Blend Energy 250g")
    m = compute_metrics(record, business_rules)

    assert m.trend_baseline_month == 2  # M1 excluded per business_rules.yaml
    assert m.growth_rate_raw == pytest.approx(0.122382, abs=1e-5)
    assert m.growth_rate_capped is True
    assert m.growth_rate == pytest.approx(0.12)


def test_bioactive_recovery_growth_rate_is_capped(records, business_rules):
    record = _get(records, "Bioactive Blend Recovery 250g")
    m = compute_metrics(record, business_rules)

    assert m.growth_rate_raw == pytest.approx(0.132118, abs=1e-5)
    assert m.growth_rate_capped is True
    assert m.growth_rate == pytest.approx(0.12)


def test_bioactive_immunity_uses_m2_baseline_not_capped(records, business_rules):
    record = _get(records, "Bioactive Blend Immunity 250g")
    m = compute_metrics(record, business_rules)

    assert m.trend_baseline_month == 2
    # baseline is demand_m2 (424), not demand_m1 (384) — confirms M1 exclusion
    assert m.demand_m2 == 424
    assert m.growth_rate_raw == pytest.approx(0.115922, abs=1e-5)
    assert not m.growth_rate_capped


def test_compute_all_metrics_emits_warning_only_for_capped_skus(records, business_rules):
    batch = compute_all_metrics(records, business_rules)
    assert len(batch.results) == 12

    capped_skus = {r.sku for r in batch.results if r.growth_rate_capped}
    assert capped_skus == {"Bioactive Blend Energy 250g", "Bioactive Blend Recovery 250g"}

    assert len(batch.warnings) == 2
    assert all("capped" in w.lower() for w in batch.warnings)
    assert any("Bioactive Blend Energy 250g" in w for w in batch.warnings)
    assert any("Bioactive Blend Recovery 250g" in w for w in batch.warnings)


# --- Formula-level unit checks on a synthetic record -----------------------


def _make_record(**overrides) -> SalesRecord:
    defaults = dict(
        sku="Synthetic SKU",
        shopify_m1=100, shopify_m2=100, shopify_m3=100, shopify_m4=100,
        amazon_m1=0, amazon_m2=0, amazon_m3=0, amazon_m4=0,
        stock_on_hand=1000, units_on_order=0,
        order_arrival_months=0, target_months_cover=2,
        retail_price_usd=10.0, trend_start_month=1,
    )
    defaults.update(overrides)
    return SalesRecord(**defaults)


def _minimal_rules(**default_overrides) -> dict:
    defaults = dict(
        supplier_lead_time_months=2,
        target_months_cover=2,
        max_monthly_growth_rate=0.12,
        order_rounding_units=100,
        projection_horizon_months=12,
    )
    defaults.update(default_overrides)
    return {"defaults": defaults, "sku_overrides": {}}


def test_order_arrival_months_zero_means_no_order_ever_added():
    """Order_Arrival_Months == 0 must never be treated as an immediate
    arrival — it means no order exists.
    """
    record = _make_record(
        shopify_m4=200, amazon_m4=0, shopify_m3=200,
        stock_on_hand=50, units_on_order=500, order_arrival_months=0,
    )
    rules = _minimal_rules()
    m = compute_metrics(record, rules)
    # If the 500 units had been (wrongly) added at k=1, stock would never
    # go negative in month 1. It must, because Order_Arrival_Months=0 means
    # "no order in flight" and available_within_lead_time must exclude it.
    assert m.projected_stockout_month == 1
    assert m.available_within_lead_time == 50  # units_on_order NOT included


def test_zero_baseline_demand_raises_metrics_error():
    record = _make_record(shopify_m1=0, amazon_m1=0, shopify_m4=100)
    rules = _minimal_rules()
    with pytest.raises(MetricsError, match="baseline"):
        compute_metrics(record, rules)


def test_zero_m3_demand_raises_metrics_error():
    record = _make_record(shopify_m3=0, amazon_m3=0)
    rules = _minimal_rules()
    with pytest.raises(MetricsError, match="M3"):
        compute_metrics(record, rules)


def test_trend_classification_boundaries():
    stalling = _make_record(shopify_m3=1000, shopify_m4=1015)  # 1.5% growth
    steady = _make_record(shopify_m3=1000, shopify_m4=1050)    # 5% growth
    accelerating = _make_record(shopify_m3=1000, shopify_m4=1100)  # 10% growth
    rules = _minimal_rules()

    assert compute_metrics(stalling, rules).trend == "stalling"
    assert compute_metrics(steady, rules).trend == "steady"
    assert compute_metrics(accelerating, rules).trend == "accelerating"


def test_reorder_quantity_rounds_up_to_order_rounding_units():
    rules = _minimal_rules(order_rounding_units=50)
    record = _make_record(stock_on_hand=0, units_on_order=0)
    m = compute_metrics(record, rules)
    assert m.reorder_quantity % 50 == 0
    assert m.units_needed > 0
    assert m.reorder_quantity >= m.units_needed


def test_missing_policy_defaults_raises_metrics_error():
    record = _make_record()
    rules = {"defaults": {"max_monthly_growth_rate": 0.12}, "sku_overrides": {}}
    with pytest.raises(MetricsError, match="target_months_cover"):
        compute_metrics(record, rules)
