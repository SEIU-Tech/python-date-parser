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

This library is a thin wrapper around the existing Rust `dateparser`.  It was
mostly created with the aid of an AI assistant.  Although commit messages
attribute this Claude, the underlying model used was a slightly customized
version of MiniMax M2.7 that was hosted by a smaller AI vendor. I simply used
the Claude Code CLI as a way of interacting with the code and model.

This library is **thousands of times** faster than any pure-Python library for
a similar task.  It is around 50x faster than similar capability in 
`pandas.to_datetime(..., format="mixed")`.


## Design

This Python module exposes three parsing functions. The Rust library created
for this binding adds very little to the capabilities of crates it uses.

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
preserves the local timezone offset.

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
functions, of the pure-Python [`dateparser`](https://pypi.org/project/dateparser/), 
and of [Pandas](https://pandas.pydata.org):

```bash
usage: benchmark.py [-h] [--examples EXAMPLES]
                    [--library {date_parser,date_parser_list,...}]
                    [--iterations ITERATIONS] [--warmup WARMUP] [--verbose]
                    [--quiet]

Benchmark date_parser (the local Rust extension) and other libraries

options:
  -h, --help            show this help message and exit
  --examples EXAMPLES   Path to the examples file (default: /media/dmertz/DQM-
                        Backup/SEIU/gnosis-date-parser/tests/data/examples.txt)
  --library, -l {date_parser,date_parser_list,date_parser_series,dateparser,pandas,all}
                        Which library to benchmark (default: all)
  --iterations, -n ITERATIONS
                        Number of timed iterations (default: 5)
  --warmup, -w WARMUP   Number of warmup iterations to run before timing
                        (default: 1)
  --verbose, -v         Show per-mismatch detail (raw input, expected, actual)
                        in verification reports. Default: summary only.
  --quiet, -q           Print only one line per library — ``<label>:
                        <throughput>`` — and suppress the header, verification
                        report, and speedup comparison. Useful for scripts that
                        just want the numbers.
```

On my system, at version 1.0.4, I see:

```
% uv run python bin/benchmark.py -q
date_parser — parse() per input                  : 1,164,578 d/s
date_parser — parse_list bulk API                : 1,294,147 d/s
date_parser — parse_series Polars API            : 1,992,147 d/s
dateparser (https://pypi.org/project/dateparser/):       147 d/s
pandas — pd.to_datetime(format='mixed')          :    40,363 d/s
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
