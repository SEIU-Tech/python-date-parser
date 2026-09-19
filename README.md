# gnosis-date-parser

Perform fast fuzzy parsing of dates in varying formats.

## Background

Numerous prior projects to perform fuzzy date parsing are available on PyPI.
The best of these appears to be https://pypi.org/project/dateparser/, but I
have not tried all of them.  All of those I have seen are written in
pure-Python, and consequentially are too slow to use when the requirement is to
parse tabular data with hundreds of thousands of dates in an interactive manner
(e.g. at the time of data upload to a website where an immediate response is
needed).

In many cases—arguably including the one motivating creation of this
library—transferring the slow operation to a background batch operation is a
reasonable approach.  However, I learned of the very fast Rust library 
`dateparser` (https://crates.io/crates/dateparser), which is as powerful in 
recognizing many date formats as are any of the Python libraries I explored.  

What I have used historically is the fuzzy date matching in Pandas; this is 
_good_, but for numerous other reasons, I wish to move away from Pandas (mostly
in favor of Polars).  However, one area where Pandas currently shines above
Polars is in parsing heterogenous dates defined in the same column of source
data.  Yes, that is bad data and the provider should do better. In the real
world, a lot of data looks that way.

This library is a thin wrapper around the existing Rust `dateparser` and 
`chrono`.  It was mostly created with the aid of an AI assistant.  Although
commit messages attribute this Claude, the underlying model used was a slightly
customized version of MiniMax M2.7 that was hosted by a smaller AI vendor. I 
simply used the Claude Code CLI as a way of interacting with the code and 
model.

This library is **thousands of times** faster than any pure-Python library for
a similar task.  I have not yet benchmarked it, but I believe it is also 10x+
faster than similar capability in `pandas.to_datetime(..., format="mixed")`.


## Design

The underlying Rust extension exposes three parsing functions. The Rust
library created for this binding adds very little to the capabilities of
crates it uses.

- `parse(raw: str) -> str | None` — accepts a single raw date string
  and returns its ISO-8601 representation (or `None` for inputs the
  parser can't handle).

- `parse_list(raw_dates: list[str]) -> list[str | None]` — accepts a
  list of raw date strings and returns a list of ISO-8601 strings (or
  `None` for inputs the parser can't handle). The returned list matches
  the input length and order. This is the bulk-list path: one Rust
  call for the whole list.

- `parse_series(s: pl.Series) -> pl.Series` — accepts a Polars Series
  of string dtype and returns a Polars Series of `pl.Datetime("ns")`
  (naive UTC, nanosecond precision). Unparseable strings become null
  values in the result. The Polars path works directly on the
  underlying Arrow buffer via 
  [`pyo3-polars`](https://docs.rs/pyo3-polars/0.28.0/pyo3_polars/),
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
pip install gnosis-date-parser
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

| `--library`           | API exercised             | Calls per iteration |
| --------------------- | --------------------------| ------------------- |
| `date_parser`         | `parse(s)` (per input)    | `len(raw_inputs)`   |
| `date_parser_list`    | `parse_list(list)` (bulk) | `1`                 |
| `date_parser_series`  | `parse_series(s)` (Arrow) | `1`                 |
| `dateparser`          | `dateparser.parse(s)`     | `len(raw_inputs)`   |

After each timed run the script verifies the parsed output against the
expected ISO-8601 values in `tests/data/examples.txt` and prints any
mismatches.

On my system, at version 1.0, I see:

```
date_parser (Rust extension, this project) — parse() per input:
  total parses:   475
  failed parses:  0
  elapsed:        0.000 s
  throughput:     1,153,021 dates/sec

verification: 95/95 inputs matched expected

date_parser (Rust extension, this project) — parse_list bulk API:
  total parses:   475
  failed parses:  0
  elapsed:        0.000 s
  throughput:     1,297,230 dates/sec

verification: 95/95 inputs matched expected

date_parser (Rust extension, this project) — parse_series Polars API:
  total parses:   475
  failed parses:  0
  elapsed:        0.000 s
  throughput:     2,001,053 dates/sec

verification: 95/95 inputs matched expected

dateparser (Python reference, https://pypi.org/project/dateparser/):
  total parses:   475
  failed parses:  0
  elapsed:        3.247 s
  throughput:     146 dates/sec

verification: 10/95 inputs did not match expected

[... to be fair, examples were mainly drawn from the underlying Rust crate ...]

speedup (date_parser vs dateparser): 7882.0x faster
```

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
