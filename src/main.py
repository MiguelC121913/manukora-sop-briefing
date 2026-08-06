"""CLI entry point for the Manukora S&OP briefing pipeline.

Currently supports:
    --naive   Run the "naive" baseline experiment: send the raw sales CSV
              directly to Claude with a simple prompt and no deterministic
              engine involved. Saves the verbatim response to
              output/v1_naive_output.md. See prompts/v1_initial.md and
              prompts/ITERATION_LOG.md for what this is testing and why.

The full pipeline (deterministic engine -> facts payload -> narrative,
via loader.py / metrics.py / rules.py / narrative.py) is wired up in a
later phase.
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
    and save the response to output/v1_naive_output.md byte-for-byte.

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

    output_path = REPO_ROOT / "output" / "v1_naive_output.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(output_text, encoding="utf-8")
    print(
        f"Saved verbatim response to {output_path.relative_to(REPO_ROOT)} "
        f"({len(output_text)} chars, stop_reason={response.stop_reason})"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Manukora S&OP briefing pipeline")
    parser.add_argument(
        "--naive",
        action="store_true",
        help="Run the naive baseline experiment (raw CSV straight to Claude, no engine). "
        "Saves output/v1_naive_output.md.",
    )
    args = parser.parse_args()

    if args.naive:
        run_naive_baseline()
        return

    parser.print_help()


if __name__ == "__main__":
    main()
