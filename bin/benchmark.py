#!/usr/bin/env python3
"""Benchmark date_parser (the local Rust extension) and dateparser (the
external Python reference implementation) against the formats in examples.txt.

By default both libraries are benchmarked so the throughput can be compared
side-by-side. Use --library to select a single library. After each library's
timed run, its output is verified against the expected ISO-8601 values from
the second column of the examples file and any mismatches are listed.

Usage:
    uv run python bin/benchmark.py                # both libraries
    uv run python bin/benchmark.py --library date_parser
    uv run python bin/benchmark.py --library dateparser
    uv run python bin/benchmark.py -n 10          # 10 timed iterations
    uv run python bin/benchmark.py --examples path/to/examples.txt
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum, auto
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

# A literal 'YYYY-MM-DD' has no time component; the expected column in
# examples.txt almost always supplies one (typically '00:00:00'), but we
# defensively handle either form when parsing it.
_DATE_ONLY = re.compile(r"\d{4}-\d{2}-\d{2}")


@dataclass(frozen=True)
class Example:
    """One row of the examples file: a raw input and its expected output."""

    raw: str
    expected: str  # "None" indicates the parser is expected to reject it


class MismatchKind(Enum):
    """Why a parsed datetime doesn't agree with the expected value."""

    EXPECTED_NONE_GOT_VALUE = auto()  # expected unparseable, got a value
    EXPECTED_VALUE_GOT_NONE = auto()  # expected a value, got None
    INSTANT_DIFFERS = auto()          # both parsed, but UTC instants differ
    EXCEPTION = auto()                # parser raised instead of returning
    EXPECTED_UNPARSEABLE = auto()     # expected string wasn't recognizable


@dataclass(frozen=True)
class Mismatch:
    """One row where the parser produced something other than the expected."""

    index: int
    example: Example
    kind: MismatchKind
    actual_repr: str  # human-readable representation of what was produced


def load_examples(path: Path) -> list[Example]:
    """Return (raw, expected) pairs from ``path``.

    Blank lines and lines whose first non-whitespace character is ``#`` are
    treated as comments and skipped. Lines missing the expected column
    (anything without a tab) are also skipped — they can't participate in
    the verification pass even though they still serve as benchmark inputs.
    """
    examples: list[Example] = []
    for raw_line in path.read_text().splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # Each test case is: <raw>\t<expected iso-8601>.
        # Use split with maxsplit=1 so the raw input itself can contain tabs
        # in pathological cases; in practice the column is tab-separated.
        parts = stripped.split("\t", 1)
        if len(parts) < 2:
            continue
        examples.append(Example(raw=parts[0].strip(), expected=parts[1].strip()))
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


def parse_expected_instant(expected: str) -> datetime | None:
    """Convert an expected ISO-8601 string into a UTC datetime, or ``None``.

    The literal string ``"None"`` (the marker for inputs the parser
    should reject) returns ``None``. Naive datetimes (no offset in the
    expected string) are interpreted as UTC: the alternative — treating
    them as the local timezone of whoever generated the file — would
    couple the verifier to that machine's timezone and make the report
    shift between CI runners. Strings that don't look like an ISO-8601
    datetime also return ``None`` so the caller can flag the row.
    """
    if expected == "None":
        return None
    s = expected
    if _DATE_ONLY.fullmatch(s):
        s += " 00:00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def to_actual_instant(name: str, actual: object) -> datetime | None:
    """Convert a parser's raw output into a UTC datetime, or ``None``.

    ``date_parser`` returns a JSON array of strings or ``null``; the
    first element is unwrapped. ``dateparser`` returns a datetime or
    ``None``. Both naive and timezone-aware outputs are normalized to
    UTC so they can be compared against :func:`parse_expected_instant`.
    """
    if name == "date_parser":
        if not isinstance(actual, str):
            return None
        try:
            decoded = json.loads(actual)
        except json.JSONDecodeError:
            return None
        if not isinstance(decoded, list) or not decoded:
            return None
        first = decoded[0]
        if first is None:
            return None
        return parse_expected_instant(first)
    if name == "dateparser":
        if actual is None or not isinstance(actual, datetime):
            return None
        if actual.tzinfo is None:
            return actual.replace(tzinfo=timezone.utc)
        return actual.astimezone(timezone.utc)
    return None


def format_actual(name: str, actual: object) -> str:
    """Render a parser's raw output as a single-line string for display."""
    if name == "date_parser":
        if isinstance(actual, str):
            try:
                decoded = json.loads(actual)
            except json.JSONDecodeError:
                return repr(actual)
            if isinstance(decoded, list) and decoded:
                inner = decoded[0]
                return "null" if inner is None else str(inner)
        return repr(actual)
    if name == "dateparser":
        if actual is None:
            return "null"
        if isinstance(actual, datetime):
            return actual.isoformat(" ", timespec="microseconds")
        return repr(actual)
    return repr(actual)


def verify_parser(
    name: str,
    parse_fn: Callable[[str], object],
    examples: list[Example],
) -> list[Mismatch]:
    """Run ``parse_fn`` on each example and compare against the expected value.

    Comparison happens in UTC: both sides are reduced to a
    :class:`~datetime.datetime` with ``tzinfo=timezone.utc``. Naive
    expected values are treated as UTC for this comparison. An empty
    returned list means every parsed output matched the expected value.
    """
    mismatches: list[Mismatch] = []
    for i, ex in enumerate(examples, start=1):
        try:
            actual = parse_fn(ex.raw)
        except Exception as exc:  # benchmark/verify must not abort on parse errors
            mismatches.append(
                Mismatch(
                    index=i,
                    example=ex,
                    kind=MismatchKind.EXCEPTION,
                    actual_repr=f"<raised {type(exc).__name__}: {exc}>",
                )
            )
            continue

        expected_dt = parse_expected_instant(ex.expected)
        actual_dt = to_actual_instant(name, actual)

        # Classify the expected column. ``ex.expected == "None"`` is the
        # literal marker; any other unparseable string is a data problem
        # in the examples file and gets its own mismatch kind so it isn't
        # silently swallowed.
        expected_is_none_literal = ex.expected == "None"
        expected_unparseable = (
            not expected_is_none_literal and expected_dt is None
        )

        if expected_unparseable and actual_dt is None:
            continue  # both unparseable; nothing to compare
        if expected_unparseable:
            mismatches.append(
                Mismatch(
                    i, ex, MismatchKind.EXPECTED_UNPARSEABLE, format_actual(name, actual)
                )
            )
            continue
        if expected_is_none_literal and actual_dt is None:
            continue  # match: expected unparseable, parser also returned None
        if expected_is_none_literal:
            mismatches.append(
                Mismatch(
                    i, ex, MismatchKind.EXPECTED_NONE_GOT_VALUE, format_actual(name, actual)
                )
            )
            continue
        if actual_dt is None:
            mismatches.append(
                Mismatch(
                    i, ex, MismatchKind.EXPECTED_VALUE_GOT_NONE, format_actual(name, actual)
                )
            )
            continue
        if expected_dt != actual_dt:
            mismatches.append(
                Mismatch(i, ex, MismatchKind.INSTANT_DIFFERS, format_actual(name, actual))
            )
            continue
        # else: the UTC instants match.
    return mismatches


def print_verification(
    name: str, total: int, mismatches: list[Mismatch]
) -> None:
    """Print a verification report for one library."""
    if not mismatches:
        print(f"verification: {total}/{total} inputs matched expected")
        return

    print(
        f"verification: {len(mismatches)}/{total} inputs did not match expected"
    )
    print()
    for m in mismatches:
        print(f"  [{m.index}] {m.example.raw!r}")
        print(f"        expected: {m.example.expected!r}")
        print(f"        actual:   {m.actual_repr}")
        print()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if not args.examples.exists():
        print(f"examples file not found: {args.examples}", file=sys.stderr)
        return 1

    examples = load_examples(args.examples)
    if not examples:
        print(f"no examples found in {args.examples}", file=sys.stderr)
        return 1

    # The timed benchmark only needs the raw column; the verification
    # pass uses both columns, so we keep the full Example objects here.
    raw_inputs = [ex.raw for ex in examples]

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

        # Verification runs after the timed loop so the throughput numbers
        # above reflect only parsing work, not the comparison overhead.
        mismatches = verify_parser(lib, parse_fn, examples)
        print_verification(lib, len(examples), mismatches)
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
