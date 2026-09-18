# python-date-parser

Perform fast fuzzy parsing of dates in varying formats.

## Status

The Rust extension exposes three parsing functions:

- `parse(raw: str) -> str | None` — accepts a single raw date string
  and returns its ISO-8601 representation (or `None` for inputs the
  parser can't handle). No JSON encoding round-trip.

- `parse_list(raw_dates: list[str]) -> list[str | None]` — accepts a
  list of raw date strings and returns a list of ISO-8601 strings (or
  `None` for inputs the parser can't handle). The returned list matches
  the input length and order. This is the bulk-list path: one Rust
  call for the whole list, no JSON encoding involved.

- `parse_series(s: pl.Series) -> pl.Series` — accepts a Polars Series
  of string dtype and returns a Polars Series of `pl.Datetime("ns")`
  (naive UTC, nanosecond precision). Unparseable strings become null
  values in the result. The Polars path works directly on the
  underlying Arrow buffer via [`pyo3-polars`](https://docs.rs/pyo3-polars/0.28.0/pyo3_polars/),
  so there is no Python-level iteration or list materialization.

All three paths use the same parsing logic via the
[`dateparser`](https://docs.rs/dateparser/0.3.1/dateparser/) crate.

```python
>>> import date_parser
>>> date_parser.parse("2026-01-01")
'2026-01-01 00:00:00+00:00'
>>> date_parser.parse("garbage") is None
True
>>> date_parser.parse_list(["2026-01-01", "garbage", "06/15/2024"])
['2026-01-01 00:00:00+00:00', None, '2024-06-15 00:00:00+00:00']

>>> import polars as pl
>>> date_parser.parse_series(pl.Series(["2026-01-01", "garbage"])).dtype
Datetime('ns')
```

**Note:** parsed datetimes are normalized to UTC (`+00:00`), matching the
behavior of the `dateparser` Rust crate. The Python `dateparser` library
preserves the original timezone offset; we don't (yet).

Pure-numeric inputs (e.g. `"1511648546"`) are interpreted as Unix
timestamps and forced to UTC: 10 digits = seconds, 13 = milliseconds,
16 = microseconds, 19 = nanoseconds. The Python `dateparser` library
interprets these in the local timezone instead.

## Installation

```bash
pip install date-parser
```

## Benchmarking

`bin/benchmark.py` measures throughput of the three `date_parser`
entry points side-by-side against the Python
[`dateparser`](https://pypi.org/project/dateparser/) reference:

```bash
# Benchmark all four libraries (the Rust extension has three modes)
uv run python bin/benchmark.py

# Benchmark a single library
uv run python bin/benchmark.py --library date_parser
uv run python bin/benchmark.py --library date_parser_list
uv run python bin/benchmark.py --library date_parser_series
uv run python bin/benchmark.py --library dateparser

# 10 timed iterations instead of the default 5
uv run python bin/benchmark.py -n 10
```

The `--library` choices map to the underlying APIs as follows:

| `--library`           | API exercised                              | Calls per iteration |
| --------------------- | ------------------------------------------ | ------------------- |
| `date_parser`         | `date_parser.parse(s)` (per input)         | `len(raw_inputs)`   |
| `date_parser_list`    | `date_parser.parse_list(list)` (bulk)      | `1`                 |
| `date_parser_series`  | `date_parser.parse_series(s)` (Polars/Arrow) | `1`               |
| `dateparser`          | `dateparser.parse(s)` (per input, Python)  | `len(raw_inputs)`   |

After each timed run the script verifies the parsed output against the
expected ISO-8601 values in `tests/data/examples.txt` and prints any
mismatches.

## Local development

The project uses [uv](https://docs.astral.sh/uv/) for environment and
dependency management.

```bash
# Create a venv and install dev dependencies (maturin, pytest, ruff,
# mypy, mkdocs-material, ipython).
uv sync --extra dev

# Compile the Rust extension and install it editable into the venv.
uv run maturin develop --release

# Run the smoke tests (parse, parse_list, parse_series).
uv run pytest -q

# Lint and typecheck.
uv run ruff check .
uv run mypy bin tests date_parser
```

## License

BSD-2-Clause. See [`LICENSE`](LICENSE).
