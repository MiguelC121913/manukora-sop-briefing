"""Facts payload construction and narrative generation.

This is the ONLY module in the codebase that calls the Anthropic API. Every
number that reaches an executive originates in loader.py -> metrics.py ->
rules.py; this module's job is strictly to (1) assemble those already-computed
numbers into a JSON "facts payload," (2) hand that payload to Claude with the
v2 prompt to be turned into prose, and (3) verify after the fact that every
dollar figure and unit quantity in the prose actually traces back to the
payload. It never computes a number itself.

Step 3 is not optional and not just documentation: `assert_narrative_numbers_verified`
raises NarrativeValidationError, naming the exact unverified figure, if the
model writes a number that isn't anywhere in the payload it was given.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from src.loader import SalesRecord
from src.metrics import SKUMetrics
from src.rules import RulesBatch

REPO_ROOT = Path(__file__).resolve().parent.parent
NARRATIVE_MODEL = "claude-sonnet-4-6"
V2_PROMPT_PATH = REPO_ROOT / "prompts" / "v2_improved.md"


class NarrativeValidationError(Exception):
    """Raised when the generated narrative contains a dollar figure or unit
    quantity that does not appear anywhere in the facts payload it was given
    — i.e. the model computed or invented a number instead of quoting one.
    """


# --- Facts payload construction --------------------------------------------


def _month_labels(m1_label: str | None) -> list[str | None]:
    """['December 2025', 'January 2026', 'February 2026', 'March 2026'] from
    an M1 calendar label — computed so the payload can hand the model an
    explicit calendar label for M1-M4 instead of leaving it to translate a
    bare month number (`trend_baseline_month`, "M2", etc.) into a calendar
    month itself (see prompts/v2_improved.md constraint 11 — that's exactly
    how "M2" got narrated as "February" instead of "January" in an earlier
    draft; see ITERATION_LOG.md's v2->v3 note).
    """
    if not m1_label:
        return [None, None, None, None]
    start = datetime.strptime(m1_label, "%B %Y")
    labels: list[str | None] = []
    for i in range(4):
        month_index = start.month - 1 + i
        year = start.year + month_index // 12
        month = month_index % 12 + 1
        labels.append(datetime(year, month, 1).strftime("%B %Y"))
    return labels


def build_facts_payload(
    records: list[SalesRecord],
    metrics_results: list[SKUMetrics],
    rules_batch: RulesBatch,
    business_rules: dict[str, Any],
    data_quality_warnings: list[str],
    metrics_warnings: list[str],
) -> dict[str, Any]:
    """Assemble the JSON facts payload Claude will receive.

    Every monetary figure is rounded to the nearest whole dollar *in the
    payload itself* (not just for display) — that's the number the narrative
    is expected to quote verbatim, and it's what post-generation validation
    checks against. Rounding twice (once here, differently in the narrative)
    would make honest quoting look like a validation failure.
    """
    records_by_sku = {r.sku: r for r in records}
    recs_by_sku = {r.sku: r for r in rules_batch.recommendations}
    period = business_rules.get("period", {}) or {}
    defaults = business_rules.get("defaults", {}) or {}
    month_labels = _month_labels(period.get("m1_label"))

    sku_payloads: list[dict[str, Any]] = []
    total_revenue_opportunity = 0
    total_stock_on_hand = 0
    total_units_on_order = 0
    total_excess_retail_value = 0

    for m in metrics_results:
        record = records_by_sku[m.sku]
        rec = recs_by_sku[m.sku]

        revenue_opportunity = round(m.revenue_opportunity_monthly)
        excess_value = round(m.excess_retail_value)

        total_revenue_opportunity += revenue_opportunity
        total_stock_on_hand += record.stock_on_hand
        total_units_on_order += record.units_on_order
        total_excess_retail_value += excess_value

        sku_payloads.append(
            {
                "sku": m.sku,
                "retail_price_usd": record.retail_price_usd,
                "demand": {
                    "m1": m.demand_m1,
                    "m2": m.demand_m2,
                    "m3": m.demand_m3,
                    "m4": m.demand_m4,
                },
                "trend_baseline_month": m.trend_baseline_month,
                "trend_baseline_label": month_labels[m.trend_baseline_month - 1],
                "trend": m.trend,
                "mom_growth_pct": round(m.mom_growth * 100, 1),
                "shopify_mom_growth_pct": (
                    round(m.shopify_mom_growth * 100, 1) if m.shopify_mom_growth is not None else None
                ),
                "amazon_mom_growth_pct": (
                    round(m.amazon_mom_growth * 100, 1) if m.amazon_mom_growth is not None else None
                ),
                "growth_rate_pct": round(m.growth_rate * 100, 1),
                "growth_rate_capped": m.growth_rate_capped,
                "baseline_demand": m.baseline_demand,
                "projected_demand": m.projected_demand,
                "stock_on_hand": record.stock_on_hand,
                "units_on_order": record.units_on_order,
                "current_cover_months": round(m.current_cover_months, 2),
                "cover_with_po_months": round(m.cover_with_po_months, 2),
                "current_cover_days": round(m.current_cover_days, 1),
                "projected_stockout_month": m.projected_stockout_month,
                "supplier_lead_time_months": m.supplier_lead_time_months,
                "target_months_cover_policy": m.target_months_cover_policy,
                "reorder_quantity_raw": m.reorder_quantity,
                "final_reorder_quantity": rec.final_reorder_quantity,
                "suppression_reason": rec.suppression_reason,
                "tension_flag": rec.tension_flag,
                "tension_supporting_figures": rec.tension_supporting_figures,
                "recommended_action": rec.recommended_action,
                "notes": rec.notes,
                "is_overstocked": m.is_overstocked,
                "excess_units": m.excess_units,
                "excess_retail_value_usd": excess_value,
                "revenue_opportunity_monthly_usd": revenue_opportunity,
            }
        )

    priority_reorder_list = [
        {
            "sku": entry["sku"],
            "reorder_quantity": entry["reorder_quantity"],
            "revenue_at_risk_monthly_usd": round(entry["revenue_at_risk_monthly"]),
            "current_cover_days": entry["current_cover_days"],
            "projected_stockout_month": entry["projected_stockout_month"],
        }
        for entry in rules_batch.priority_reorder_list
    ]
    total_revenue_at_risk = sum(e["revenue_at_risk_monthly_usd"] for e in priority_reorder_list)

    # Filtered AND pre-sorted in Python — both are arithmetic-adjacent tasks
    # this project's core principle already says not to delegate to the
    # model (see ITERATION_LOG.md's v2->v3 note on the sorting half of this).
    # The filter has two conditions:
    #   - within stock_at_risk_horizon_months (see business_rules.yaml
    #     defaults): a SKU stocking out further out than the longest supplier
    #     lead time in the dataset isn't a decision that's urgent *today*.
    #   - not is_overstocked: an overstocked SKU showing a distant stockout
    #     month is not "at risk" — without this, MGO 100+ 250g appeared in
    #     Stock at Risk (month 8) while Judgment Calls simultaneously said to
    #     defer its inbound PO for being overstocked — a direct contradiction
    #     an executive would notice immediately. Propolis Tincture is *not*
    #     overstocked (its reorder is suppressed for a different reason — a
    #     planned phase-out, not excess inventory) so it correctly still
    #     passes this filter and keeps appearing here with its suppression
    #     noted in Judgment Calls, which is the intended, non-contradictory
    #     case: "at risk of stocking out" and "not being reordered" can both
    #     be true at once for a SKU being wound down on purpose.
    stock_at_risk_horizon = defaults.get("stock_at_risk_horizon_months", 4)
    stock_at_risk_list = sorted(
        (
            s
            for s in sku_payloads
            if s["projected_stockout_month"] is not None
            and s["projected_stockout_month"] <= stock_at_risk_horizon
            and not s["is_overstocked"]
        ),
        key=lambda s: s["revenue_opportunity_monthly_usd"],
        reverse=True,
    )
    stock_at_risk_list = [
        {
            "sku": s["sku"],
            "projected_stockout_month": s["projected_stockout_month"],
            "revenue_opportunity_monthly_usd": s["revenue_opportunity_monthly_usd"],
        }
        for s in stock_at_risk_list
    ]

    return {
        "period": {
            "m1_label": period.get("m1_label"),
            "m4_label": period.get("m4_label"),
            "current_month": period.get("current_month"),
        },
        "portfolio_totals": {
            "sku_count": len(metrics_results),
            "total_stock_on_hand_units": total_stock_on_hand,
            "total_units_on_order": total_units_on_order,
            "total_revenue_opportunity_monthly_usd": total_revenue_opportunity,
            "total_revenue_at_risk_monthly_usd": total_revenue_at_risk,
            "total_excess_retail_value_usd": total_excess_retail_value,
            "skus_needing_reorder": len(priority_reorder_list),
            "skus_overstocked": sum(1 for m in metrics_results if m.is_overstocked),
            "skus_with_tension_flag": sum(1 for r in rules_batch.recommendations if r.tension_flag),
            "skus_with_suppressed_reorder": sum(
                1 for r in rules_batch.recommendations if r.suppression_reason
            ),
        },
        "priority_reorder_list": priority_reorder_list,
        "stock_at_risk_list": stock_at_risk_list,
        "skus": sku_payloads,
        "data_quality_warnings": data_quality_warnings,
        "metrics_warnings": metrics_warnings,
        "rules_warnings": rules_batch.warnings,
        "business_rules_assumptions": {
            "max_monthly_growth_rate_pct": round(defaults.get("max_monthly_growth_rate", 0) * 100, 1),
            "order_rounding_units": defaults.get("order_rounding_units"),
            "projection_horizon_months": defaults.get("projection_horizon_months"),
            "default_supplier_lead_time_months": defaults.get("supplier_lead_time_months"),
            "default_target_months_cover": defaults.get("target_months_cover"),
            "stock_at_risk_horizon_months": stock_at_risk_horizon,
        },
    }


def write_facts_payload(payload: dict[str, Any], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


# --- Prompt loading ----------------------------------------------------------


def _parse_v2_prompt(template: str) -> tuple[str, str]:
    """Split prompts/v2_improved.md into (system_prompt, user_message_template).

    The system prompt is everything under '## System' up to (not including)
    '## User message template'. The user message template is the fenced code
    block under that second heading, containing the
    '<facts_payload.json inserted verbatim>' placeholder.
    """
    system_marker = "## System"
    user_marker = "## User message template"
    if system_marker not in template or user_marker not in template:
        raise ValueError(
            f"prompts/v2_improved.md is missing '{system_marker}' or '{user_marker}'."
        )

    system_text = template.split(system_marker, 1)[1].split(user_marker, 1)[0].strip()
    if system_text.endswith("---"):
        system_text = system_text[:-3].rstrip()

    user_section = template.split(user_marker, 1)[1]
    fence_parts = user_section.split("```")
    if len(fence_parts) < 3:
        raise ValueError("prompts/v2_improved.md's user message template has no fenced code block.")
    user_message_template = fence_parts[1].strip()

    return system_text, user_message_template


def load_v2_prompt(path: str | Path = V2_PROMPT_PATH) -> tuple[str, str]:
    return _parse_v2_prompt(Path(path).read_text(encoding="utf-8"))


# --- Narrative generation -----------------------------------------------------


@dataclass
class NarrativeResult:
    narrative: str
    stop_reason: str
    unverified_claims: list[str]


def generate_narrative(payload: dict[str, Any], prompt_path: str | Path = V2_PROMPT_PATH) -> tuple[str, str]:
    """Call Claude once with the v2 system prompt and the facts payload.
    Returns (narrative_text, stop_reason). Raises SystemExit with a clear
    message if the anthropic package or API key isn't available.
    """
    try:
        import anthropic
    except ImportError as exc:
        raise SystemExit(
            "The 'anthropic' package is required. Activate your venv and run: "
            "pip install -r requirements.txt"
        ) from exc

    try:
        from dotenv import load_dotenv

        load_dotenv(REPO_ROOT / ".env")
    except ImportError:
        pass

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit(
            "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add your key, then re-run."
        )

    system_prompt, user_message_template = load_v2_prompt(prompt_path)
    user_message = user_message_template.replace(
        "<facts_payload.json inserted verbatim>", json.dumps(payload, indent=2)
    )

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=NARRATIVE_MODEL,
        max_tokens=4096,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )
    narrative = next(b.text for b in response.content if b.type == "text")
    return narrative, response.stop_reason


def save_narrative(narrative: str, path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(narrative, encoding="utf-8")


# --- Post-generation numeric validation ---------------------------------------

# Three claim shapes, tried in order at each position: a dollar amount, a
# comma-grouped number of any size (typical of quantities/revenue >= 1000
# written in prose or a table), or a number explicitly labeled "unit(s)"
# (catches quantities under 1000 that wouldn't otherwise get a comma, e.g.
# "800 units"). Plain small numbers (month indices, percentages, SKU counts)
# deliberately fall outside this — the brief scopes validation to "every
# dollar figure and unit quantity," not every digit in the document.
_CLAIM_RE = re.compile(
    r"(?P<dollar>\$\s?\d{1,3}(?:,\d{3})+(?:\.\d+)?|\$\s?\d+(?:\.\d+)?)"
    r"|(?P<comma_number>\b\d{1,3}(?:,\d{3})+\b)"
    r"|(?P<unit_number>\b\d+(?:\.\d+)?\s*units?\b)",
    re.IGNORECASE,
)


def _flatten_numbers(obj: Any) -> set[float]:
    """Collect every int/float leaf value anywhere in a JSON-like structure."""
    numbers: set[float] = set()
    if isinstance(obj, bool):
        return numbers  # bool is an int subclass; not a meaningful "number" here
    if isinstance(obj, (int, float)):
        numbers.add(float(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            numbers |= _flatten_numbers(v)
    elif isinstance(obj, list):
        for v in obj:
            numbers |= _flatten_numbers(v)
    return numbers


def _extract_claimed_numbers(narrative: str) -> list[tuple[str, float]]:
    """Every (raw matched text, parsed numeric value) claim in the narrative."""
    claims: list[tuple[str, float]] = []
    for match in _CLAIM_RE.finditer(narrative):
        raw = match.group(0)
        digits = re.sub(r"[^\d.]", "", raw)
        if not digits:
            continue
        claims.append((raw, float(digits)))
    return claims


def validate_narrative_numbers(narrative: str, payload: dict[str, Any]) -> list[str]:
    """Return a list of claim strings in `narrative` whose numeric value does
    not appear anywhere in `payload`. Empty list means every dollar figure
    and unit quantity in the narrative is traceable to the facts payload.
    """
    valid_numbers = _flatten_numbers(payload)
    unmatched = []
    for raw, value in _extract_claimed_numbers(narrative):
        if value not in valid_numbers:
            unmatched.append(f"{raw!r} (parsed as {value:g})")
    return unmatched


def assert_narrative_numbers_verified(narrative: str, payload: dict[str, Any]) -> None:
    """Fail loudly if the narrative contains a figure not present in the
    facts payload — this is the verification story: implemented, not just
    described.
    """
    unmatched = validate_narrative_numbers(narrative, payload)
    if unmatched:
        raise NarrativeValidationError(
            "Narrative contains figures not found anywhere in the facts payload "
            "(possible hallucination):\n" + "\n".join(f"  - {u}" for u in unmatched)
        )
