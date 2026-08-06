"""CSV parsing and schema validation for the S&OP briefing engine.

This module is deliberately "dumb": it turns the raw mock_sales.csv rows into
typed, validated SalesRecord objects and nothing more. It does not compute
trends, reorder quantities, or any other business math — that belongs to
metrics.py. Its two jobs are:

1. Fail loudly (LoaderError) on malformed input rather than silently
   producing garbage numbers downstream.
2. Detect known data-quality inconsistencies between the raw CSV and the
   business rules config, surface them as `data_quality_warnings`, and
   attach enough metadata (`trend_start_month`) for metrics.py to handle
   them correctly instead of ignoring them.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

EXPECTED_COLUMNS = [
    "SKU",
    "Shopify_M1",
    "Shopify_M2",
    "Shopify_M3",
    "Shopify_M4",
    "Amazon_M1",
    "Amazon_M2",
    "Amazon_M3",
    "Amazon_M4",
    "Stock_On_Hand",
    "Units_On_Order",
    "Order_Arrival_Months",
    "Target_Months_Cover",
    "Retail_Price_USD",
]

# Columns that must never be negative — negative stock/units/price indicates
# corrupted source data, not a legitimate business state.
NON_NEGATIVE_INT_COLUMNS = [
    "Shopify_M1", "Shopify_M2", "Shopify_M3", "Shopify_M4",
    "Amazon_M1", "Amazon_M2", "Amazon_M3", "Amazon_M4",
    "Stock_On_Hand", "Units_On_Order",
    "Order_Arrival_Months", "Target_Months_Cover",
]


class LoaderError(Exception):
    """Raised when the CSV or business rules config fails validation.

    Deliberately distinct from generic exceptions so callers (and tests) can
    assert on load-time failures without catching unrelated bugs.
    """


@dataclass(frozen=True)
class SalesRecord:
    """One validated row of monthly sales/inventory data for a single SKU."""

    sku: str
    shopify_m1: int
    shopify_m2: int
    shopify_m3: int
    shopify_m4: int
    amazon_m1: int
    amazon_m2: int
    amazon_m3: int
    amazon_m4: int
    stock_on_hand: int
    units_on_order: int
    order_arrival_months: int
    target_months_cover: int
    retail_price_usd: float
    # 1-indexed month (within M1-M4) from which trend calculations should
    # start for this SKU. Defaults to 1 (use all four months). Set >1 when
    # business_rules.yaml marks an earlier month as not a valid baseline
    # (e.g. a SKU launched mid-window and has spurious pre-launch sales).
    trend_start_month: int = 1


@dataclass
class LoadResult:
    records: list[SalesRecord]
    data_quality_warnings: list[str] = field(default_factory=list)


def load_business_rules(path: str | Path) -> dict[str, Any]:
    """Load and parse business_rules.yaml. Raises LoaderError if unreadable."""
    path = Path(path)
    if not path.exists():
        raise LoaderError(f"Business rules file not found: {path}")
    try:
        with path.open("r", encoding="utf-8") as f:
            rules = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise LoaderError(f"Could not parse business rules YAML at {path}: {exc}") from exc
    if not isinstance(rules, dict):
        raise LoaderError(f"Business rules file at {path} did not parse to a mapping.")
    return rules


def _parse_int(raw: str, *, sku: str, column: str, row_num: int) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise LoaderError(
            f"Row {row_num} (SKU '{sku}'): column '{column}' has non-numeric "
            f"value '{raw}'. Expected an integer."
        ) from None


def _parse_float(raw: str, *, sku: str, column: str, row_num: int) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        raise LoaderError(
            f"Row {row_num} (SKU '{sku}'): column '{column}' has non-numeric "
            f"value '{raw}'. Expected a number."
        ) from None


def load_sales_data(
    csv_path: str | Path,
    business_rules: dict[str, Any] | None = None,
) -> LoadResult:
    """Parse and validate mock_sales.csv into typed SalesRecords.

    Args:
        csv_path: path to the sales CSV.
        business_rules: parsed business_rules.yaml (see load_business_rules).
            Used only to detect the trend-baseline data-quality issue below
            and to tag records with `trend_start_month`. If omitted, every
            SKU defaults to trend_start_month=1 and no such warning is
            produced (schema/value validation still runs).

    Returns:
        LoadResult with validated records and any data_quality_warnings.

    Raises:
        LoaderError: on missing/extra columns, unparseable values, or
            negative quantities. The loader never fails silently.
    """
    csv_path = Path(csv_path)
    if not csv_path.exists():
        raise LoaderError(f"Sales data file not found: {csv_path}")

    business_rules = business_rules or {}
    sku_overrides: dict[str, Any] = business_rules.get("sku_overrides", {}) or {}

    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames or []

        missing = [c for c in EXPECTED_COLUMNS if c not in header]
        extra = [c for c in header if c not in EXPECTED_COLUMNS]
        if missing or extra:
            problems = []
            if missing:
                problems.append(f"missing columns: {missing}")
            if extra:
                problems.append(f"unexpected columns: {extra}")
            raise LoaderError(
                f"Schema mismatch in {csv_path}: {'; '.join(problems)}. "
                f"Expected exactly: {EXPECTED_COLUMNS}"
            )

        records: list[SalesRecord] = []
        skus_with_m1_baseline_issue: list[tuple[str, int]] = []

        for row_num, row in enumerate(reader, start=2):  # header is line 1
            sku = (row.get("SKU") or "").strip()
            if not sku:
                raise LoaderError(f"Row {row_num}: SKU is empty.")

            int_values: dict[str, int] = {}
            for col in NON_NEGATIVE_INT_COLUMNS:
                value = _parse_int(row[col], sku=sku, column=col, row_num=row_num)
                if value < 0:
                    raise LoaderError(
                        f"Row {row_num} (SKU '{sku}'): column '{col}' is negative "
                        f"({value}). Negative stock/units/months indicates corrupted data."
                    )
                int_values[col] = value

            retail_price = _parse_float(
                row["Retail_Price_USD"], sku=sku, column="Retail_Price_USD", row_num=row_num
            )
            if retail_price < 0:
                raise LoaderError(
                    f"Row {row_num} (SKU '{sku}'): Retail_Price_USD is negative ({retail_price})."
                )

            override = sku_overrides.get(sku, {}) or {}
            trend_start_month = int(override.get("trend_baseline_month", 1))

            if trend_start_month > 1 and (int_values["Shopify_M1"] > 0 or int_values["Amazon_M1"] > 0):
                m1_total = int_values["Shopify_M1"] + int_values["Amazon_M1"]
                skus_with_m1_baseline_issue.append((sku, m1_total))

            records.append(
                SalesRecord(
                    sku=sku,
                    shopify_m1=int_values["Shopify_M1"],
                    shopify_m2=int_values["Shopify_M2"],
                    shopify_m3=int_values["Shopify_M3"],
                    shopify_m4=int_values["Shopify_M4"],
                    amazon_m1=int_values["Amazon_M1"],
                    amazon_m2=int_values["Amazon_M2"],
                    amazon_m3=int_values["Amazon_M3"],
                    amazon_m4=int_values["Amazon_M4"],
                    stock_on_hand=int_values["Stock_On_Hand"],
                    units_on_order=int_values["Units_On_Order"],
                    order_arrival_months=int_values["Order_Arrival_Months"],
                    target_months_cover=int_values["Target_Months_Cover"],
                    retail_price_usd=retail_price,
                    trend_start_month=trend_start_month,
                )
            )

    warnings: list[str] = []
    if skus_with_m1_baseline_issue:
        m1_label = (business_rules.get("period", {}) or {}).get("m1_label", "M1")
        sku_list = ", ".join(
            f"{sku} ({units} units)" for sku, units in skus_with_m1_baseline_issue
        )
        warnings.append(
            f"Data quality: {sku_list} report {m1_label} sales despite "
            f"business_rules.yaml marking a later month as their trend baseline "
            f"(trend_baseline_month override, launched mid-window). This is an "
            f"inconsistency in the source data — {m1_label} has been excluded from "
            f"trend calculations for these SKUs rather than silently ignored."
        )

    return LoadResult(records=records, data_quality_warnings=warnings)
