from pathlib import Path

import pytest

from src.loader import LoaderError, load_business_rules, load_sales_data

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_CSV = REPO_ROOT / "data" / "mock_sales.csv"
BUSINESS_RULES_YAML = REPO_ROOT / "config" / "business_rules.yaml"

VALID_HEADER = (
    "SKU,Shopify_M1,Shopify_M2,Shopify_M3,Shopify_M4,"
    "Amazon_M1,Amazon_M2,Amazon_M3,Amazon_M4,"
    "Stock_On_Hand,Units_On_Order,Order_Arrival_Months,"
    "Target_Months_Cover,Retail_Price_USD"
)


def _write_csv(tmp_path: Path, rows: list[str]) -> Path:
    path = tmp_path / "sales.csv"
    path.write_text("\n".join([VALID_HEADER, *rows]) + "\n", encoding="utf-8")
    return path


# --- Happy path against the real mock data -------------------------------


def test_loads_all_twelve_records_from_mock_data():
    rules = load_business_rules(BUSINESS_RULES_YAML)
    result = load_sales_data(DATA_CSV, business_rules=rules)
    assert len(result.records) == 12
    assert {r.sku for r in result.records} == {
        "Manuka Honey MGO 100+ 250g",
        "Manuka Honey MGO 263+ 250g",
        "Manuka Honey MGO 263+ 500g",
        "Manuka Honey MGO 514+ 250g",
        "Manuka Honey MGO 514+ 500g",
        "Manuka Honey MGO 850+ 250g",
        "Manuka Honey MGO 850+ 500g",
        "Manuka Honey MGO 1700+ 100g",
        "Propolis Tincture 30ml",
        "Bioactive Blend Immunity 250g",
        "Bioactive Blend Energy 250g",
        "Bioactive Blend Recovery 250g",
    }


def test_parses_a_known_row_correctly():
    rules = load_business_rules(BUSINESS_RULES_YAML)
    result = load_sales_data(DATA_CSV, business_rules=rules)
    row = next(r for r in result.records if r.sku == "Manuka Honey MGO 100+ 250g")
    assert row.shopify_m1 == 568
    assert row.amazon_m4 == 388
    assert row.stock_on_hand == 6400
    assert row.units_on_order == 2000
    assert row.order_arrival_months == 1
    assert row.target_months_cover == 2
    assert row.retail_price_usd == pytest.approx(24.99)
    assert row.trend_start_month == 1  # no override for this SKU


def test_bioactive_skus_get_trend_start_month_two_from_config():
    rules = load_business_rules(BUSINESS_RULES_YAML)
    result = load_sales_data(DATA_CSV, business_rules=rules)
    bioactive_skus = {
        "Bioactive Blend Immunity 250g",
        "Bioactive Blend Energy 250g",
        "Bioactive Blend Recovery 250g",
    }
    for record in result.records:
        if record.sku in bioactive_skus:
            assert record.trend_start_month == 2
        else:
            assert record.trend_start_month == 1


def test_data_quality_warning_surfaced_for_bioactive_m1_inconsistency():
    rules = load_business_rules(BUSINESS_RULES_YAML)
    result = load_sales_data(DATA_CSV, business_rules=rules)
    assert len(result.data_quality_warnings) == 1
    warning = result.data_quality_warnings[0]
    assert "Bioactive Blend Immunity 250g" in warning
    assert "Bioactive Blend Energy 250g" in warning
    assert "Bioactive Blend Recovery 250g" in warning
    assert "excluded from trend" in warning.lower()


def test_no_warnings_without_business_rules():
    # Without business rules, the loader has no basis to flag the M1
    # inconsistency and must not fabricate one.
    result = load_sales_data(DATA_CSV, business_rules=None)
    assert result.data_quality_warnings == []
    assert all(r.trend_start_month == 1 for r in result.records)


# --- Schema / value validation --------------------------------------------


def test_missing_column_raises_loader_error(tmp_path):
    bad_header = VALID_HEADER.replace("Retail_Price_USD", "")
    path = tmp_path / "sales.csv"
    path.write_text(bad_header + "\n", encoding="utf-8")
    with pytest.raises(LoaderError, match="missing columns"):
        load_sales_data(path)


def test_extra_column_raises_loader_error(tmp_path):
    path = tmp_path / "sales.csv"
    path.write_text(VALID_HEADER + ",Extra_Column\n", encoding="utf-8")
    with pytest.raises(LoaderError, match="unexpected columns"):
        load_sales_data(path)


def test_non_numeric_value_raises_loader_error(tmp_path):
    row = "Test SKU,not_a_number,552,620,644,356,376,404,388,6400,2000,1,2,24.99"
    path = _write_csv(tmp_path, [row])
    with pytest.raises(LoaderError, match="non-numeric"):
        load_sales_data(path)


def test_negative_stock_raises_loader_error(tmp_path):
    row = "Test SKU,568,552,620,644,356,376,404,388,-1,2000,1,2,24.99"
    path = _write_csv(tmp_path, [row])
    with pytest.raises(LoaderError, match="negative"):
        load_sales_data(path)


def test_empty_sku_raises_loader_error(tmp_path):
    row = ",568,552,620,644,356,376,404,388,6400,2000,1,2,24.99"
    path = _write_csv(tmp_path, [row])
    with pytest.raises(LoaderError, match="SKU is empty"):
        load_sales_data(path)


def test_missing_file_raises_loader_error(tmp_path):
    with pytest.raises(LoaderError, match="not found"):
        load_sales_data(tmp_path / "does_not_exist.csv")


def test_missing_business_rules_file_raises_loader_error(tmp_path):
    with pytest.raises(LoaderError, match="not found"):
        load_business_rules(tmp_path / "does_not_exist.yaml")


def test_real_business_rules_file_loads():
    rules = load_business_rules(BUSINESS_RULES_YAML)
    assert rules["defaults"]["target_months_cover"] == 2
    assert rules["sku_overrides"]["Propolis Tincture 30ml"]["phase_out_quarter"] == "2026-Q2"
