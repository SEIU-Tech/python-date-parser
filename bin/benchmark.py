#!/usr/bin/env python3
"""Benchmark date_parser (the local Rust extension) against the formats in
examples.txt, optionally alongside the dateparser and pandas reference
implementations.

By default every available library is benchmarked so the throughput can be
compared side-by-side. Use --library to select a single library. After each
library's timed run, its output is verified against the expected ISO-8601
values from the second column of the examples file. By default the
verification report shows only the summary count; pass -v/--verbose to
list each mismatched input, its expected value, and the actual value.

Usage:
    uv run python bin/benchmark.py                # every available library
    uv run python bin/benchmark.py --library date_parser
    uv run python bin/benchmark.py --library dateparser
    uv run python bin/benchmark.py --library pandas
    uv run python bin/benchmark.py -n 10          # 10 timed iterations
    uv run python bin/benchmark.py -v              # show per-mismatch detail
    uv run python bin/benchmark.py -q              # one throughput line per lib
    uv run python bin/benchmark.py --examples path/to/examples.txt
"""

from __future__ import annotations

import argparse
import re
import sys
import time
import warnings
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum, auto
from pathlib import Path

try:
    import dateparser
except ImportError:
    # ``dateparser`` is a dev-only optional dependency used only as a
    # reference library inside this benchmark. The benchmark still
    # runs (against ``date_parser`` alone) when it's not installed;
    # main() emits a warning if the user asked to benchmark it.
    dateparser = None  # type: ignore[assignment]

try:
    import pandas as pd
except ImportError:
    # ``pandas`` is another dev-only reference library; same treatment
    # as ``dateparser`` — main() filters it out of the selected list
    # and warns when the user asked to benchmark it.
    pd = None  # type: ignore[assignment]

import polars as pl

import date_parser

# tests/data/examples.txt, resolved relative to this script's repo root.
here = Path(__file__).resolve()
DEFAULT_EXAMPLES = here.parent.parent / "tests" / "data" / "examples.txt"

# Need today for time-only formats (timezone is whatever `date_parser` says)
TODAY = date_parser.parse("01:01:01").split(" ")[0]
PST = date_parser.parse("01:01:01 PST").split(" ")[0]
DAY = TODAY.split("-")[-1]

# Library identifiers and their human-readable labels for the report.
LIBRARIES: dict[str, str] = {
    "date_parser": "date_parser — parse() per input",
    "date_parser_list": "date_parser — parse_list bulk API",
    "date_parser_series": "date_parser — parse_series Polars API",
    "dateparser": "dateparser (https://pypi.org/project/dateparser/)",
    "pandas": "pandas — pd.to_datetime(format='mixed')",
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
    INSTANT_DIFFERS = auto()  # both parsed, but UTC instants differ
    EXCEPTION = auto()  # parser raised instead of returning
    EXPECTED_UNPARSEABLE = auto()  # expected string wasn't recognizable


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
        target = parts[1].strip()
        target = target.replace("<TODAY>", TODAY)
        target = target.replace("<PST>", PST)
        target = target.replace("<DAY>", DAY)
        examples.append(
            Example(
                raw=parts[0].strip(),
                expected=target,
            )
        )
    return examples


def make_parser(name: str, raw_inputs: list[str]) -> Callable[[str], object]:
    """Return a parser function for the named library.

    The returned callable accepts a single ``str`` and returns whatever the
    underlying library produces. Any exception raised by the library is
    propagated to the caller, which decides whether to count it as a
    failure or abort.

    ``raw_inputs`` is captured by closure for the bulk APIs
    (``date_parser_list``, ``date_parser_series``): those libraries don't
    fit a per-input loop, so the benchmark builds the batch artifact once
    and re-parses it on every call.
    """
    match name:
        case "date_parser":
            # The single-string API takes one input and returns either
            # an ISO-8601 string or ``None`` — no JSON encode/decode
            # round-trip per call.
            def parse(raw: str) -> object:
                return date_parser.parse(raw)

            return parse
        case "date_parser_list":
            # The list API takes the whole list in one call, so the
            # per-input ``raw`` argument is intentionally ignored.
            def parse(_raw: str) -> object:
                return date_parser.parse_list(raw_inputs)

            return parse
        case "date_parser_series":
            series = pl.Series(raw_inputs)

            # The Series API operates on the whole Series per call, so
            # the per-input ``raw`` argument is intentionally ignored.
            def parse(_raw: str) -> object:
                return date_parser.parse_series(series)

            return parse
        case "dateparser":
            # Defensive: main() filters ``dateparser`` out of the
            # selected library list when the import above failed, so
            # this branch should only run when the module is available.
            # The explicit check gives a clear error if a future caller
            # bypasses that filter.
            if dateparser is None:
                raise RuntimeError(
                    "dateparser not installed; install the dev extras to benchmark it"
                )
            mod = dateparser

            # The Python module only operates on a single string.
            def parse(raw: str) -> object:
                return mod.parse(raw)  # type: ignore

            return parse
        case "pandas":
            # Same defensive pattern as the dateparser branch above.
            if pd is None:
                raise RuntimeError(
                    "pandas not installed; install the dev extras to benchmark it"
                )
            mod = pd

            # ``format="mixed"`` lets pandas infer a format per input;
            # ``errors="coerce"`` returns ``NaT`` for unparseable
            # inputs instead of raising, mirroring the per-input
            # behavior of the other parsers.
            #
            # ``format="mixed"`` also emits a ``FutureWarning`` on
            # inputs that include unrecognized timezone abbreviations
            # (e.g. "PST"). The warning is about a planned pandas API
            # change — irrelevant to a throughput benchmark — so
            # suppress it within the parse call rather than letting it
            # spam the output.
            def parse(raw: str) -> object:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", FutureWarning)
                    return mod.to_datetime(raw, format="mixed", errors="coerce")

            return parse
        case _:
            msg = f"unknown library: {name!r}; expected one of {sorted(LIBRARIES)}"
            raise ValueError(msg)


def is_bulk(name: str) -> bool:
    """Return True if the library operates on the whole batch in one call.

    The ``parse_series`` Polars API and the ``parse()`` bulk-list API
    both parse every input in a single call rather than per-element, so
    the benchmark loop and throughput accounting have to treat them
    differently from the per-input APIs.
    """
    return name in ("date_parser_series", "date_parser_list")


def run_calls(
    parse: Callable[[str], object],
    raw_inputs: Iterable[str],
    bulk: bool,
) -> int:
    """Drive the parser; return the failure count.

    For per-input libraries we call ``parse`` once for every raw input.
    For bulk libraries (``date_parser_series``, ``date_parser_list``) we
    call ``parse`` once per ``run_calls`` invocation — it parses the
    whole batch in one shot and the per-input argument is ignored.
    """
    if bulk:
        try:
            parse("")
        except Exception:  # benchmark must not abort on parse errors
            return 1
        return 0
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
    bulk: bool = False,
) -> tuple[float, int, int]:
    """Run ``iterations`` timed passes plus ``warmup`` warmup passes.

    ``total_parses`` always reports *elements* processed (per-input
    libraries count 1 per call; bulk libraries count the batch length
    per call) so throughput figures are comparable across the two
    styles.

    Returns ``(elapsed_seconds, total_parses, failures)``.
    """
    for _ in range(warmup):
        run_calls(parse, raw_inputs, bulk)

    total_parses = 0
    failures = 0
    start = time.perf_counter()
    for _ in range(iterations):
        # Accumulate per-iteration failures so a bad input never poisons
        # the whole run.
        failures += run_calls(parse, raw_inputs, bulk)
        # Both styles process one batch-worth of elements per iteration:
        # per-input libraries do ``len(raw_inputs)`` calls each handling
        # one element; bulk libraries do a single call that processes
        # them all at once.
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
        choices=(
            "date_parser",
            "date_parser_list",
            "date_parser_series",
            "dateparser",
            "pandas",
            "all",
        ),
        default="all",
        help="Which library to benchmark (default: all)",
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
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help=(
            "Show per-mismatch detail (raw input, expected, actual) "
            "in verification reports. Default: summary only."
        ),
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help=(
            "Print only one line per library — ``<label>: <throughput>`` "
            "— and suppress the header, verification report, and speedup "
            "comparison. Useful for scripts that just want the numbers."
        ),
    )
    return parser.parse_args(argv)


def format_throughput(parses: int, elapsed: float, unit: str = "dates/sec") -> str:
    if elapsed > 0:
        return f"{parses / elapsed:,.0f} {unit}"
    return f"inf {unit}"


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

    ``date_parser`` (single-string API) returns either an ISO-8601
    string or ``None``; we normalize the string the same way as the
    expected column. ``dateparser`` returns a datetime or ``None``.
    ``pandas`` returns a ``pd.Timestamp`` (which subclasses
    ``datetime``) or ``pd.NaT``; we use ``pd.isna`` to detect the
    NaT case. Both naive and timezone-aware outputs are normalized
    to UTC so they can be compared against :func:`parse_expected_instant`.
    """
    if name == "date_parser":
        if actual is None:
            return None
        if not isinstance(actual, str):
            return None
        return parse_expected_instant(actual)
    if name == "dateparser":
        if actual is None or not isinstance(actual, datetime):
            return None
        if actual.tzinfo is None:
            return actual.replace(tzinfo=timezone.utc)
        return actual.astimezone(timezone.utc)
    if name == "pandas":
        # main() guarantees ``pd`` is not None when this branch runs.
        assert pd is not None
        if pd.isna(actual):
            return None
        if not isinstance(actual, datetime):
            return None
        if actual.tzinfo is None:
            return actual.replace(tzinfo=timezone.utc)
        return actual.astimezone(timezone.utc)
    return None


def format_actual(name: str, actual: object) -> str:
    """Render a parser's raw output as a single-line string for display."""
    if name == "date_parser":
        if actual is None:
            return "null"
        if isinstance(actual, str):
            return actual
        return repr(actual)
    if name == "dateparser":
        if actual is None:
            return "null"
        if isinstance(actual, datetime):
            return actual.isoformat(" ", timespec="microseconds")
        return repr(actual)
    if name == "pandas":
        # main() guarantees ``pd`` is not None when this branch runs.
        assert pd is not None
        if actual is None or pd.isna(actual):
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
        expected_unparseable = not expected_is_none_literal and expected_dt is None

        if expected_unparseable and actual_dt is None:
            continue  # both unparseable; nothing to compare
        if expected_unparseable:
            mismatches.append(
                Mismatch(
                    i,
                    ex,
                    MismatchKind.EXPECTED_UNPARSEABLE,
                    format_actual(name, actual),
                )
            )
            continue
        if expected_is_none_literal and actual_dt is None:
            continue  # match: expected unparseable, parser also returned None
        if expected_is_none_literal:
            mismatches.append(
                Mismatch(
                    i,
                    ex,
                    MismatchKind.EXPECTED_NONE_GOT_VALUE,
                    format_actual(name, actual),
                )
            )
            continue
        if actual_dt is None:
            mismatches.append(
                Mismatch(
                    i,
                    ex,
                    MismatchKind.EXPECTED_VALUE_GOT_NONE,
                    format_actual(name, actual),
                )
            )
            continue
        if expected_dt != actual_dt:
            mismatches.append(
                Mismatch(
                    i,
                    ex,
                    MismatchKind.INSTANT_DIFFERS,
                    format_actual(name, actual),
                )
            )
            continue
        # else: the UTC instants match.
    return mismatches


def bulk_actual_instants(name: str, actual: object) -> list[datetime | None] | None:
    """Unwrap a bulk-parser result into a list of UTC datetimes (or ``None``).

    ``date_parser_list`` returns a Python list of ISO-8601 strings (or
    ``None``); we normalize each entry to UTC the same way
    :func:`to_actual_instant` does for per-input results.
    ``date_parser_series`` returns a Polars ``Series`` of naive UTC
    ``pl.Datetime("ns")``; we materialize it to a Python list and
    promote each ``datetime`` to UTC-aware form for comparison.

    Returns ``None`` if the shape of ``actual`` doesn't match what the
    named library is expected to produce; the caller surfaces that as a
    single ``EXCEPTION``-style mismatch so the report still says
    *something* useful.
    """
    if name == "date_parser_list":
        if not isinstance(actual, list):
            return None
        out: list[datetime | None] = []
        for entry in actual:
            if entry is None:
                out.append(None)
            elif isinstance(entry, str):
                out.append(parse_expected_instant(entry))
            else:
                return None
        return out
    if name == "date_parser_series":
        # ``pl.Series`` instances expose ``.to_list()`` and we already
        # typed the import lazily via the closure above; use duck typing
        # so this helper doesn't have to import Polars itself.
        to_list = getattr(actual, "to_list", None)
        if not callable(to_list):
            return None
        try:
            entries = to_list()
        except Exception:  # benchmark must not abort on parse errors
            return None
        out = []
        for entry in entries:
            if entry is None:
                out.append(None)
            elif isinstance(entry, datetime):
                if entry.tzinfo is None:
                    out.append(entry.replace(tzinfo=timezone.utc))
                else:
                    out.append(entry.astimezone(timezone.utc))
            else:
                out.append(None)
        return out
    return None


def verify_bulk(
    name: str,
    parse_fn: Callable[[str], object],
    examples: list[Example],
) -> list[Mismatch]:
    """Run a bulk parser once and compare each slot against the expected.

    The per-input ``raw`` argument to ``parse_fn`` is ignored — bulk
    parsers operate on the whole batch they captured at construction.
    Comparison is in UTC, identical to :func:`verify_parser`.
    """
    try:
        actual = parse_fn("")
    except Exception as exc:  # benchmark/verify must not abort on parse errors
        return [
            Mismatch(
                index=0,
                example=examples[0] if examples else Example(raw="", expected="None"),
                kind=MismatchKind.EXCEPTION,
                actual_repr=f"<raised {type(exc).__name__}: {exc}>",
            )
        ]

    actual_list = bulk_actual_instants(name, actual)
    if actual_list is None:
        return [
            Mismatch(
                index=0,
                example=examples[0] if examples else Example(raw="", expected="None"),
                kind=MismatchKind.EXCEPTION,
                actual_repr=(f"<unrecognized result:{format_actual(name, actual)!r}>"),
            )
        ]

    mismatches: list[Mismatch] = []
    for i, ex in enumerate(examples, start=1):
        if i - 1 >= len(actual_list):
            mismatches.append(
                Mismatch(
                    index=i,
                    example=ex,
                    kind=MismatchKind.EXPECTED_VALUE_GOT_NONE,
                    actual_repr="<missing>",
                )
            )
            continue
        expected_dt = parse_expected_instant(ex.expected)
        actual_dt = actual_list[i - 1]
        expected_is_none_literal = ex.expected == "None"
        expected_unparseable = not expected_is_none_literal and expected_dt is None
        if expected_unparseable and actual_dt is None:
            continue
        if expected_unparseable:
            mismatches.append(
                Mismatch(
                    i,
                    ex,
                    MismatchKind.EXPECTED_UNPARSEABLE,
                    repr(actual_list[i - 1]),
                )
            )
            continue
        if expected_is_none_literal and actual_dt is None:
            continue
        if expected_is_none_literal:
            mismatches.append(
                Mismatch(
                    i,
                    ex,
                    MismatchKind.EXPECTED_NONE_GOT_VALUE,
                    repr(actual_list[i - 1]),
                )
            )
            continue
        if actual_dt is None:
            mismatches.append(
                Mismatch(i, ex, MismatchKind.EXPECTED_VALUE_GOT_NONE, "null"),
            )
            continue
        if expected_dt != actual_dt:
            mismatches.append(
                Mismatch(i, ex, MismatchKind.INSTANT_DIFFERS, str(actual_list[i - 1]))
            )
    return mismatches


def print_verification(
    _name: str, total: int, mismatches: list[Mismatch], verbose: bool = False
) -> None:
    """Print a verification report for one library.

    With ``verbose=False`` (the default), only the summary line is
    printed. With ``verbose=True``, each mismatch's raw input,
    expected value, and actual output is listed below the summary so
    the run can be diagnosed without re-running the benchmark.
    """
    if not mismatches:
        print(f"verification: {total}/{total} inputs matched expected")
        return

    print(f"verification: {len(mismatches)}/{total} inputs did not match expected")
    if not verbose:
        return

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

    selected: list[str] = list(LIBRARIES) if args.library == "all" else [args.library]

    # The ``dateparser`` and ``pandas`` reference libraries live in
    # [project.optional-dependencies].dev; warn and skip each one
    # the user selected (or that ``all`` includes) when its dev
    # extra isn't installed.
    missing_extras: dict[str, str] = {
        "dateparser": (
            "the 'dateparser' library is not installed; skipping it. "
            "Install the dev extras (`pip install gnosis-date-parser[dev]`) "
            "to include it in the benchmark."
        ),
        "pandas": (
            "the 'pandas' library is not installed; skipping it. "
            "Install the dev extras (`pip install gnosis-date-parser[dev]`) "
            "to include it in the benchmark."
        ),
    }
    available = {"dateparser": dateparser, "pandas": pd}
    for name, message in missing_extras.items():
        if name in selected and available[name] is None:
            warnings.warn(message, stacklevel=2)
            selected = [lib for lib in selected if lib != name]
    if not selected:
        print("no libraries left to benchmark", file=sys.stderr)
        return 1

    if not args.quiet:
        print(f"examples file:    {args.examples}")
        print(f"distinct inputs:  {len(raw_inputs)}")
        print(f"iterations:       {args.iterations} ({args.warmup} warmup)")
        print()

    results: dict[str, tuple[float, int, int]] = {}
    for lib in selected:
        parse_fn = make_parser(lib, raw_inputs)

        elapsed, parses, failures = benchmark(
            parse_fn,
            raw_inputs,
            args.iterations,
            args.warmup,
            bulk=is_bulk(lib),
        )
        results[lib] = (elapsed, parses, failures)

        if args.quiet:
            continue  # quiet-mode summary is printed below, after alignment

        print(f"{LIBRARIES[lib]}:")
        print(f"  total parses:   {parses}")
        print(f"  failed parses:  {failures}")
        print(f"  elapsed:        {elapsed:.3f} s")
        print(f"  throughput:     {format_throughput(parses, elapsed)}")
        print()

        # Verification runs after the timed loop so the throughput numbers
        # above reflect only parsing work, not the comparison overhead.
        # Bulk libraries (date_parser_list, date_parser_series) return a
        # whole-batch result so they get a per-batch verifier below;
        # per-input libraries use the per-input verifier.
        if is_bulk(lib):
            mismatches = verify_bulk(lib, parse_fn, examples)
        else:
            mismatches = verify_parser(lib, parse_fn, examples)
        print_verification(lib, len(examples), mismatches, verbose=args.verbose)
        print()

    if args.quiet:
        # Quiet mode prints all libraries at once so the throughput
        # columns align — easier to compare numbers by eye. Units are
        # abbreviated to ``d/s`` to keep each line compact.
        labels = [LIBRARIES[lib] for lib in selected]
        throughputs = [
            format_throughput(
                results[lib][1],
                results[lib][0],
                unit="d/s",
            )
            for lib in selected
        ]
        label_width = max(len(lbl) for lbl in labels)
        throughput_width = max(len(t) for t in throughputs)
        for lbl, t in zip(labels, throughputs, strict=True):
            print(f"{lbl:<{label_width}}: {t:>{throughput_width}}")
        return 0

    if "date_parser" in results and "dateparser" in results:
        a_elapsed, _, _ = results["date_parser"]
        b_elapsed, _, _ = results["dateparser"]
        if a_elapsed > 0 and b_elapsed > 0:
            ratio = b_elapsed / a_elapsed
            print(f"speedup (date_parser vs dateparser): {ratio:.1f}x faster")

    return 0


if __name__ == "__main__":
    sys.exit(main())
