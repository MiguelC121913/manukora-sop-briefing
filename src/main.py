"""CLI entry point for the Manukora S&OP briefing pipeline.

    --naive             Run the "naive" baseline experiment: send the raw
                         sales CSV directly to Claude with a simple prompt,
                         no deterministic engine involved. Saves the verbatim
                         response to output/history/v1_naive_output.md (kept
                         out of output/'s root so that folder never contains
                         anything but the current deliverable). See
                         prompts/v1_initial.md and prompts/ITERATION_LOG.md.

    --generate           Run the real pipeline: loader -> metrics -> rules ->
                         facts payload -> Claude (v2 prompt) -> validated
                         narrative. Writes output/facts_payload_<month>.json
                         and output/sop_briefing_<month>.md.

    --month YYYY-MM      Month label used in output filenames for --generate
                         (default: 2026-03, matching the mock data).
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
NAIVE_MODEL = "claude-sonnet-4-6"


def _extract_user_message(template: str) -> str:
    """Pull the text under '## User message' out of prompts/v1_initial.md.

    That section is written to be exactly the message sent to the API — see
    the file itself for the contract.
    """
    marker = "## User message"
    if marker not in template:
        raise ValueError(f"prompts/v1_initial.md is missing the '{marker}' section.")
    return template.split(marker, 1)[1].strip()


def run_naive_baseline() -> None:
    """Send the raw CSV to Claude with prompts/v1_initial.md's naive prompt
    and save the response to output/history/v1_naive_output.md byte-for-byte.

    This is a deliberate anti-pattern kept in the repo for comparison: no
    deterministic math, no business rules, no facts payload. The output is
    never edited or "cleaned up" after the fact — the point is to have a
    real transcript of where an unassisted LLM gets the numbers wrong.
    """
    try:
        import anthropic
    except ImportError as exc:
        raise SystemExit(
            "The 'anthropic' package is required for --naive. Activate your venv and "
            "run: pip install -r requirements.txt"
        ) from exc

    try:
        from dotenv import load_dotenv

        load_dotenv(REPO_ROOT / ".env")
    except ImportError:
        pass  # python-dotenv is a convenience; ANTHROPIC_API_KEY may already be in the env

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit(
            "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add your key, "
            "then re-run."
        )

    csv_path = REPO_ROOT / "data" / "mock_sales.csv"
    raw_csv = csv_path.read_text(encoding="utf-8")

    prompt_template = (REPO_ROOT / "prompts" / "v1_initial.md").read_text(encoding="utf-8")
    user_message = _extract_user_message(prompt_template).replace(
        "<contents of data/mock_sales.csv inserted verbatim>", raw_csv
    )

    client = anthropic.Anthropic()
    print(f"Calling Claude ({NAIVE_MODEL}) with the naive prompt — no engine, no facts payload...")
    response = client.messages.create(
        model=NAIVE_MODEL,
        max_tokens=8192,
        messages=[{"role": "user", "content": user_message}],
    )
    output_text = next(b.text for b in response.content if b.type == "text")

    output_path = REPO_ROOT / "output" / "history" / "v1_naive_output.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(output_text, encoding="utf-8")
    print(
        f"Saved verbatim response to {output_path.relative_to(REPO_ROOT)} "
        f"({len(output_text)} chars, stop_reason={response.stop_reason})"
    )


def run_pipeline(month_label: str = "2026-03") -> None:
    """The real pipeline: deterministic engine all the way to a validated,
    facts-grounded narrative. No step here computes a business number except
    metrics.py/rules.py, which already ran before narrative.py is touched.
    """
    from src.loader import load_business_rules, load_sales_data
    from src.metrics import compute_all_metrics
    from src.narrative import (
        NARRATIVE_MODEL,
        NarrativeValidationError,
        assert_narrative_numbers_verified,
        build_facts_payload,
        generate_narrative,
        save_narrative,
        write_facts_payload,
    )
    from src.rules import apply_business_rules

    business_rules = load_business_rules(REPO_ROOT / "config" / "business_rules.yaml")
    load_result = load_sales_data(REPO_ROOT / "data" / "mock_sales.csv", business_rules=business_rules)
    for w in load_result.data_quality_warnings:
        print(f"[data quality] {w}")

    metrics_batch = compute_all_metrics(load_result.records, business_rules)
    for w in metrics_batch.warnings:
        print(f"[metrics] {w}")

    rules_batch = apply_business_rules(load_result.records, metrics_batch.results, business_rules)
    for w in rules_batch.warnings:
        print(f"[rules] {w}")

    payload = build_facts_payload(
        load_result.records,
        metrics_batch.results,
        rules_batch,
        business_rules,
        load_result.data_quality_warnings,
        metrics_batch.warnings,
    )
    payload_path = REPO_ROOT / "output" / f"facts_payload_{month_label}.json"
    write_facts_payload(payload, payload_path)
    print(f"Wrote facts payload to {payload_path.relative_to(REPO_ROOT)}")

    print(f"Calling Claude ({NARRATIVE_MODEL}) with the v2 prompt + facts payload...")
    narrative, stop_reason = generate_narrative(payload)
    print(f"Received narrative ({len(narrative)} chars, stop_reason={stop_reason}). Validating numbers...")

    try:
        assert_narrative_numbers_verified(narrative, payload)
    except NarrativeValidationError as exc:
        unverified_path = REPO_ROOT / "output" / f"sop_briefing_{month_label}_UNVERIFIED.md"
        save_narrative(narrative, unverified_path)
        print(f"Saved UNVERIFIED narrative to {unverified_path.relative_to(REPO_ROOT)} for inspection.")
        raise SystemExit(f"Narrative validation FAILED:\n{exc}") from exc

    narrative_path = REPO_ROOT / "output" / f"sop_briefing_{month_label}.md"
    save_narrative(narrative, narrative_path)
    print(
        f"All figures verified against the facts payload. "
        f"Saved briefing to {narrative_path.relative_to(REPO_ROOT)}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Manukora S&OP briefing pipeline")
    parser.add_argument(
        "--naive",
        action="store_true",
        help="Run the naive baseline experiment (raw CSV straight to Claude, no engine). "
        "Saves output/history/v1_naive_output.md.",
    )
    parser.add_argument(
        "--generate",
        action="store_true",
        help="Run the full deterministic pipeline and generate a validated narrative briefing.",
    )
    parser.add_argument(
        "--month",
        default="2026-03",
        help="Month label for --generate output filenames (default: 2026-03).",
    )
    args = parser.parse_args()

    if args.naive:
        run_naive_baseline()
        return
    if args.generate:
        run_pipeline(args.month)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
