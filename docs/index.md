# python-date-parser

Fast fuzzy parsing of dates in varying formats.

## Status

The Rust extension exposes two parsing functions:

- `parse(raw_dates: list[str]) -> str` returns a JSON-encoded array of
  ISO-8601 strings.
- `parse_series(s: pl.Series) -> pl.Series` returns a Polars Series of
  `pl.Datetime("ns")`. The Polars path works directly on the underlying
  Arrow buffer via
  [`pyo3-polars`](https://docs.rs/pyo3-polars/0.28.0/pyo3_polars/),
  with no Python-level iteration.

```python
>>> import json, date_parser
>>> json.loads(date_parser.parse(["2026-01-01", "garbage", "06/15/2024"]))
['2026-01-01 00:00:00+00:00', None, '2024-06-15 00:00:00+00:00']

>>> import polars as pl
>>> date_parser.parse_series(pl.Series(["2026-01-01", "garbage"])).dtype
Datetime('ns')
```

**Note:** parsed datetimes are normalized to UTC (`+00:00`), matching the
behavior of the `dateparser` Rust crate. Pure-numeric inputs are
interpreted as Unix timestamps (10 digits = seconds, 13 = ms, 16 = µs,
19 = ns) and always treated as UTC.

## Local development

```bash
# Install dev tools (maturin, pytest, ruff, mypy, mkdocs-material)
pip install -e .[dev]

# Compile the Rust extension and install it editable into the active environment
maturin develop --release

# Run the smoke test
pytest -q
```

## License

BSD-2-Clause. See [LICENSE](https://github.com/SEIU-Tech/python-date-parser/blob/main/LICENSE).
