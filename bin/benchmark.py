#!/usr/bin/env python3
"""Benchmark the `date_parser` extension against the formats in examples.txt.

The script reads tests/data/examples.txt (configurable via --examples),
extracts the raw format strings from each non-comment, non-blank line, and
times repeated calls to ``date_parser.parse_date`` over those inputs. It
prints a small report including the throughput in dates per second.

Usage:
    uv run python bin/benchmark.py                # default settings
    uv run python bin/benchmark.py -n 10          # 10 timed iterations
    uv run python bin/benchmark.py --examples path/to/examples.txt
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import date_parser

# tests/data/examples.txt, resolved relative to this script's repo root.
DEFAULT_EXAMPLES = (
    Path(__file__).resolve().parent.parent / "tests" / "data" / "examples.txt"
)


def load_examples(path: Path) -> list[str]:
    """Return the raw format strings (first tab-separated column) from `path`.

    Blank lines and lines whose first non-whitespace character is ``#`` are
    treated as comments and skipped.
    """
    examples: list[str] = []
    for raw_line in path.read_text().splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # Each test case is: <raw>\t<expected iso-8601>.
        # Use split with maxsplit=1 so the raw input itself can contain tabs
        # in pathological cases; in practice the column is tab-separated.
        examples.append(stripped.split("\t", 1)[0])
    return examples


def run_calls(raw_inputs: list[str]) -> int:
    """Call ``date_parser.parse_date`` on each input; return the failure count."""
    failures = 0
    for raw in raw_inputs:
        try:
            date_parser.parse_date(raw)
        except Exception:  # benchmark must not abort on parse errors
            failures += 1
    return failures


def benchmark(
    raw_inputs: list[str], iterations: int, warmup: int
) -> tuple[float, int, int]:
    """Run ``iterations`` timed passes plus ``warmup`` warmup passes.

    Returns ``(elapsed_seconds, total_parses, failures)``.
    """
    for _ in range(warmup):
        run_calls(raw_inputs)

    total_parses = 0
    failures = 0
    start = time.perf_counter()
    for _ in range(iterations):
        # Accumulate per-iteration failures so a bad input never poisons
        # the whole run.
        failures += run_calls(raw_inputs)
        total_parses += len(raw_inputs)
    elapsed = time.perf_counter() - start
    return elapsed, total_parses, failures


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0] if __doc__ else None,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--examples",
        type=Path,
        default=DEFAULT_EXAMPLES,
        help=f"Path to the examples file (default: {DEFAULT_EXAMPLES})",
    )
    parser.add_argument(
        "--iterations",
        "-n",
        type=int,
        default=5,
        help="Number of timed iterations (default: 5)",
    )
    parser.add_argument(
        "--warmup",
        "-w",
        type=int,
        default=1,
        help="Number of warmup iterations to run before timing (default: 1)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if not args.examples.exists():
        print(f"examples file not found: {args.examples}", file=sys.stderr)
        return 1

    raw_inputs = load_examples(args.examples)
    if not raw_inputs:
        print(f"no examples found in {args.examples}", file=sys.stderr)
        return 1

    elapsed, total_parses, failures = benchmark(
        raw_inputs, args.iterations, args.warmup
    )
    throughput = total_parses / elapsed if elapsed > 0 else float("inf")

    print(f"examples file:    {args.examples}")
    print(f"distinct inputs:  {len(raw_inputs)}")
    print(f"iterations:       {args.iterations} ({args.warmup} warmup)")
    print(f"total parses:     {total_parses}")
    print(f"failed parses:    {failures}")
    print(f"elapsed:          {elapsed:.3f} s")
    print(f"throughput:       {throughput:,.0f} dates/sec")
    return 0


if __name__ == "__main__":
    sys.exit(main())
