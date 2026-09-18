#!/usr/bin/env python3
"""Benchmark date_parser (the local Rust extension) and dateparser (the
external Python reference implementation) against the formats in examples.txt.

By default both libraries are benchmarked so the throughput can be compared
side-by-side. Use --library to select a single library.

Usage:
    uv run python bin/benchmark.py                # both libraries
    uv run python bin/benchmark.py --library date_parser
    uv run python bin/benchmark.py --library dateparser
    uv run python bin/benchmark.py -n 10          # 10 timed iterations
    uv run python bin/benchmark.py --examples path/to/examples.txt
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable, Iterable
from pathlib import Path

# tests/data/examples.txt, resolved relative to this script's repo root.
DEFAULT_EXAMPLES = (
    Path(__file__).resolve().parent.parent / "tests" / "data" / "examples.txt"
)

# Library identifiers and their human-readable labels for the report.
LIBRARIES: dict[str, str] = {
    "date_parser": "date_parser (Rust extension, this project)",
    "dateparser": "dateparser (Python reference, https://pypi.org/project/dateparser/)",
}


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


def make_parser(name: str) -> Callable[[str], object]:
    """Return a parser function for the named library.

    The returned callable accepts a single ``str`` and returns whatever the
    underlying library produces. Any exception raised by the library is
    propagated to the caller, which decides whether to count it as a
    failure or abort.
    """
    if name == "date_parser":
        import date_parser

        def parse(raw: str) -> object:
            # The Rust extension takes a list and returns a JSON string;
            # wrap a single raw input to keep the benchmark loop uniform
            # across libraries (one call per input).
            return date_parser.parse([raw])

        return parse
    if name == "dateparser":
        import dateparser

        def parse(raw: str) -> object:
            return dateparser.parse(raw)

        return parse
    msg = f"unknown library: {name!r}; expected one of {sorted(LIBRARIES)}"
    raise ValueError(msg)


def run_calls(parse: Callable[[str], object], raw_inputs: Iterable[str]) -> int:
    """Call ``parse`` on each input; return the failure count."""
    failures = 0
    for raw in raw_inputs:
        try:
            parse(raw)
        except Exception:  # benchmark must not abort on parse errors
            failures += 1
    return failures


def benchmark(
    parse: Callable[[str], object],
    raw_inputs: list[str],
    iterations: int,
    warmup: int,
) -> tuple[float, int, int]:
    """Run ``iterations`` timed passes plus ``warmup`` warmup passes.

    Returns ``(elapsed_seconds, total_parses, failures)``.
    """
    for _ in range(warmup):
        run_calls(parse, raw_inputs)

    total_parses = 0
    failures = 0
    start = time.perf_counter()
    for _ in range(iterations):
        # Accumulate per-iteration failures so a bad input never poisons
        # the whole run.
        failures += run_calls(parse, raw_inputs)
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
        "--library",
        "-l",
        choices=("date_parser", "dateparser", "both"),
        default="both",
        help="Which library to benchmark (default: both)",
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


def format_throughput(parses: int, elapsed: float) -> str:
    if elapsed > 0:
        return f"{parses / elapsed:,.0f} dates/sec"
    return "inf dates/sec"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if not args.examples.exists():
        print(f"examples file not found: {args.examples}", file=sys.stderr)
        return 1

    raw_inputs = load_examples(args.examples)
    if not raw_inputs:
        print(f"no examples found in {args.examples}", file=sys.stderr)
        return 1

    selected: list[str] = (
        list(LIBRARIES) if args.library == "both" else [args.library]
    )

    print(f"examples file:    {args.examples}")
    print(f"distinct inputs:  {len(raw_inputs)}")
    print(f"iterations:       {args.iterations} ({args.warmup} warmup)")
    print()

    results: dict[str, tuple[float, int, int]] = {}
    for lib in selected:
        try:
            parse_fn = make_parser(lib)
        except ImportError as exc:
            print(
                f"{lib}: SKIPPED ({exc.name} is not installed; "
                f"re-run after `uv sync` to enable)",
                file=sys.stderr,
            )
            print()
            continue

        elapsed, parses, failures = benchmark(
            parse_fn, raw_inputs, args.iterations, args.warmup
        )
        results[lib] = (elapsed, parses, failures)

        print(f"{LIBRARIES[lib]}:")
        print(f"  total parses:   {parses}")
        print(f"  failed parses:  {failures}")
        print(f"  elapsed:        {elapsed:.3f} s")
        print(f"  throughput:     {format_throughput(parses, elapsed)}")
        print()

    if len(results) == 2:
        a_elapsed, _, _ = results["date_parser"]
        b_elapsed, _, _ = results["dateparser"]
        if a_elapsed > 0 and b_elapsed > 0:
            ratio = b_elapsed / a_elapsed
            print(f"speedup (date_parser vs dateparser): {ratio:.1f}x faster")

    return 0


if __name__ == "__main__":
    sys.exit(main())
