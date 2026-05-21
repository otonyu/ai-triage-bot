"""
CLI: read a JSON array of raw bug report strings, emit a JSON array of
triaged outputs.

Usage:
    python -m src.cli --input test_data/reports.json --output triaged.json
    python -m src.cli --input test_data/reports.json  # writes to stdout
    cat reports.json | python -m src.cli --stdin

Each output is one of:
    {"status": "actionable", ...}
    {"status": "needs_clarification", ...}
    {"status": "not_a_bug", ...}
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import List

from anthropic import Anthropic

from .triage import triage_report


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Transform raw user bug reports into structured tickets, clarification requests, or non-bug classifications.",
    )
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--input", "-i", help="Path to JSON file containing an array of raw report strings.")
    src.add_argument("--stdin", action="store_true", help="Read JSON array from stdin.")
    parser.add_argument("--output", "-o", help="Path to write triaged JSON array. Defaults to stdout.")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print progress to stderr.")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
        stream=sys.stderr,
    )

    # Load input
    if args.stdin:
        raw = sys.stdin.read()
    else:
        with open(args.input, "r") as f:
            raw = f.read()
    try:
        reports = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"Input is not valid JSON: {e}", file=sys.stderr)
        return 2

    if not isinstance(reports, list) or not all(isinstance(r, str) for r in reports):
        print("Input must be a JSON array of strings.", file=sys.stderr)
        return 2

    client = Anthropic()
    results = []
    for idx, report in enumerate(reports):
        logging.info("Triaging report %d/%d", idx + 1, len(reports))
        try:
            output = triage_report(report, client)
            results.append(output.model_dump(mode="json"))
        except Exception as e:
            # Don't let one bad report kill the whole batch.
            logging.exception("Failed to triage report %d: %s", idx, e)
            results.append({
                "status": "error",
                "original_report": report,
                "error": str(e),
            })

    out_json = json.dumps(results, indent=2)
    if args.output:
        with open(args.output, "w") as f:
            f.write(out_json)
        logging.info("Wrote %d results to %s", len(results), args.output)
    else:
        print(out_json)

    return 0


if __name__ == "__main__":
    sys.exit(main())
