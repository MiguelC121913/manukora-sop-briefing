from pathlib import Path

import pytest

from src.loader import load_business_rules, load_sales_data
from src.metrics import compute_all_metrics
from src.narrative import (
    NarrativeValidationError,
    _extract_claimed_numbers,
    _flatten_numbers,
    _parse_v2_prompt,
    assert_narrative_numbers_verified,
    build_facts_payload,
    validate_narrative_numbers,
)
from src.rules import apply_business_rules

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_CSV = REPO_ROOT / "data" / "mock_sales.csv"
BUSINESS_RULES_YAML = REPO_ROOT / "config" / "business_rules.yaml"
V2_PROMPT = REPO_ROOT / "prompts" / "v2_improved.md"


@pytest.fixture(scope="module")
def payload():
    business_rules = load_business_rules(BUSINESS_RULES_YAML)
    load_result = load_sales_data(DATA_CSV, business_rules=business_rules)
    metrics_batch = compute_all_metrics(load_result.records, business_rules)
    rules_batch = apply_business_rules(load_result.records, metrics_batch.results, business_rules)
    return build_facts_payload(
        load_result.records,
        metrics_batch.results,
        rules_batch,
        business_rules,
        load_result.data_quality_warnings,
        metrics_batch.warnings,
    )


def _sku(payload, sku_name):
    return next(s for s in payload["skus"] if s["sku"] == sku_name)


# --- build_facts_payload -----------------------------------------------------


def test_payload_has_twelve_skus(payload):
    assert payload["portfolio_totals"]["sku_count"] == 12
    assert len(payload["skus"]) == 12


def test_payload_propolis_shows_suppression_and_stockout(payload):
    propolis = _sku(payload, "Propolis Tincture 30ml")
    assert propolis["reorder_quantity_raw"] == 700
    assert propolis["final_reorder_quantity"] == 0
    assert propolis["suppression_reason"] == "phase_out_q2_2026_cover_above_30_days"
    assert propolis["projected_stockout_month"] == 2  # still reported despite suppression


def test_payload_priority_reorder_list_matches_expected_order(payload):
    skus_in_order = [entry["sku"] for entry in payload["priority_reorder_list"]]
    assert skus_in_order == [
        "Manuka Honey MGO 263+ 500g",
        "Manuka Honey MGO 514+ 500g",
        "Manuka Honey MGO 850+ 500g",
        "Bioactive Blend Immunity 250g",
        "Manuka Honey MGO 1700+ 100g",
        "Bioactive Blend Energy 250g",
        "Bioactive Blend Recovery 250g",
    ]
    assert "Manuka Honey MGO 263+ 250g" not in skus_in_order


def test_payload_monetary_figures_are_whole_dollars(payload):
    for entry in payload["priority_reorder_list"]:
        assert entry["revenue_at_risk_monthly_usd"] == int(entry["revenue_at_risk_monthly_usd"])
    for sku in payload["skus"]:
        assert sku["revenue_opportunity_monthly_usd"] == int(sku["revenue_opportunity_monthly_usd"])


def test_payload_carries_data_quality_and_metrics_warnings(payload):
    assert len(payload["data_quality_warnings"]) == 1
    assert "Bioactive" in payload["data_quality_warnings"][0]
    assert len(payload["metrics_warnings"]) == 2  # Energy + Recovery growth caps


def test_payload_stock_at_risk_list_filtered_and_sorted(payload):
    """stock_at_risk_list must be filtered before it's sorted (both in
    Python, never left for the narrative to do — see ITERATION_LOG.md's
    v2->v3 note on the sorting half of this, and the follow-up note on the
    filtering half): excludes anything overstocked (even if its stockout
    month is inside the horizon, and even if it's the highest-revenue SKU
    in the dataset), excludes anything outside the actionable horizon, but
    still includes a suppressed-but-not-overstocked SKU like Propolis —
    "at risk of stocking out" and "not being reordered" are both legitimately
    true for a controlled phase-out, which is not the same thing as being
    overstocked.
    """
    expected_order = [
        "Manuka Honey MGO 263+ 500g",
        "Manuka Honey MGO 514+ 500g",
        "Manuka Honey MGO 850+ 500g",
        "Bioactive Blend Immunity 250g",
        "Manuka Honey MGO 1700+ 100g",
        "Bioactive Blend Energy 250g",
        "Bioactive Blend Recovery 250g",
        "Propolis Tincture 30ml",
    ]
    skus_in_list = [e["sku"] for e in payload["stock_at_risk_list"]]
    assert skus_in_list == expected_order

    # Overstocked SKUs must never appear, regardless of revenue or stockout month
    overstocked_skus = {s["sku"] for s in payload["skus"] if s["is_overstocked"]}
    assert overstocked_skus == {
        "Manuka Honey MGO 100+ 250g",
        "Manuka Honey MGO 263+ 250g",
        "Manuka Honey MGO 514+ 250g",
        "Manuka Honey MGO 850+ 250g",
    }
    assert overstocked_skus.isdisjoint(skus_in_list)

    # Horizon respected: every remaining entry stocks out within the configured window
    horizon = payload["business_rules_assumptions"]["stock_at_risk_horizon_months"]
    for entry in payload["stock_at_risk_list"]:
        assert entry["projected_stockout_month"] <= horizon

    # Propolis (suppressed, not overstocked) still appears — different case from overstock
    assert "Propolis Tincture 30ml" in skus_in_list

    revenues = [e["revenue_opportunity_monthly_usd"] for e in payload["stock_at_risk_list"]]
    assert revenues == sorted(revenues, reverse=True)


def test_payload_bioactive_trend_baseline_label_is_january_2026(payload):
    """M2 for this dataset is January 2026 (M1 = December 2025) — the label
    must say that explicitly, not leave the model to translate "M2" itself.
    """
    for sku_name in [
        "Bioactive Blend Immunity 250g",
        "Bioactive Blend Energy 250g",
        "Bioactive Blend Recovery 250g",
    ]:
        sku = _sku(payload, sku_name)
        assert sku["trend_baseline_month"] == 2
        assert sku["trend_baseline_label"] == "January 2026"


def test_payload_non_bioactive_trend_baseline_label_is_december_2025(payload):
    sku = _sku(payload, "Manuka Honey MGO 100+ 250g")
    assert sku["trend_baseline_month"] == 1
    assert sku["trend_baseline_label"] == "December 2025"


def test_payload_bioactive_notes_forbid_m1_reference(payload):
    for sku_name in [
        "Bioactive Blend Immunity 250g",
        "Bioactive Blend Energy 250g",
        "Bioactive Blend Recovery 250g",
    ]:
        sku = _sku(payload, sku_name)
        assert any("Do not reference M1" in n for n in sku["notes"])


# --- v2 prompt parsing --------------------------------------------------------


def test_parse_v2_prompt_extracts_system_and_user_sections():
    template = V2_PROMPT.read_text(encoding="utf-8")
    system_text, user_template = _parse_v2_prompt(template)

    assert "Hard constraints" in system_text
    assert "## User message template" not in system_text
    assert not system_text.rstrip().endswith("---")

    assert "<facts_payload.json inserted verbatim>" in user_template
    assert "```" not in user_template  # fence markers stripped


# --- numeric extraction and validation ---------------------------------------


def test_extract_claimed_numbers_catches_dollars_commas_and_units():
    text = "Reorder 1,500 units of X ($40,308 at risk). Also reorder 800 units. SKU count: 12. Growth: 15%."
    claims = _extract_claimed_numbers(text)
    values = {v for _, v in claims}
    assert 1500.0 in values
    assert 40308.0 in values
    assert 800.0 in values
    # small unlabeled numbers (12, 15) must NOT be treated as claims
    assert 12.0 not in values
    assert 15.0 not in values


def test_extract_claimed_numbers_handles_dollar_amount_without_thousands_separator():
    """Regression test: a 4+ digit dollar figure with no comma (e.g. "$40308"
    instead of "$40,308") must be captured whole, not truncated to its first
    three digits ("$403") with the rest silently dropped.
    """
    claims = _extract_claimed_numbers("Revenue at risk: $40308 this month.")
    values = {v for _, v in claims}
    assert 40308.0 in values
    assert 403.0 not in values


def test_flatten_numbers_walks_nested_structures():
    payload = {"a": 1, "b": {"c": 2.5, "d": [3, 4, {"e": 5}]}, "f": "not a number", "g": True}
    numbers = _flatten_numbers(payload)
    assert numbers == {1.0, 2.5, 3.0, 4.0, 5.0}  # bool 'g' excluded


def test_validate_narrative_numbers_passes_when_all_figures_are_in_payload(payload):
    entry = payload["priority_reorder_list"][0]
    narrative = (
        f"Reorder {entry['reorder_quantity']} units of {entry['sku']}, "
        f"protecting ${entry['revenue_at_risk_monthly_usd']} in monthly revenue at risk."
    )
    unmatched = validate_narrative_numbers(narrative, payload)
    assert unmatched == []


def test_validate_narrative_numbers_flags_fabricated_figure(payload):
    narrative = "Reorder 9,999 units immediately — this protects $123,456 in revenue."
    unmatched = validate_narrative_numbers(narrative, payload)
    assert len(unmatched) == 2
    assert any("9,999" in u for u in unmatched)
    assert any("123,456" in u for u in unmatched)


def test_assert_narrative_numbers_verified_raises_with_the_bad_figure(payload):
    narrative = "We recommend ordering 9,999 units."
    with pytest.raises(NarrativeValidationError, match="9,999"):
        assert_narrative_numbers_verified(narrative, payload)


def test_assert_narrative_numbers_verified_passes_silently_for_clean_narrative(payload):
    total = payload["portfolio_totals"]["total_revenue_at_risk_monthly_usd"]
    narrative = f"Total revenue at risk this month is ${total}."
    assert_narrative_numbers_verified(narrative, payload)  # must not raise
