# python-date-parser

Perform fast fuzzy parsing of dates in varying formats.

## Status

The Rust extension parses raw date strings into ISO-8601 representations
using the [`dateparser`](https://docs.rs/dateparser/0.3.1/dateparser/)
crate. The single public function is `parse(raw_dates: list[str]) -> str`
— it accepts a list of raw date strings and returns a JSON-encoded array
of ISO-8601 strings (or `null` for inputs the parser can't handle).

```python
>>> import json, date_parser
>>> json.loads(date_parser.parse(["2026-01-01", "garbage", "06/15/2024"]))
['2026-01-01 00:00:00+00:00', None, '2024-06-15 00:00:00+00:00']
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

## Local development

The project uses [uv](https://docs.astral.sh/uv/) for environment and
dependency management.

```bash
# Create a venv and install dev dependencies (maturin, pytest, ruff,
# mypy, mkdocs-material).
uv sync --extra dev

# Compile the Rust extension and install it editable into the venv.
uv run maturin develop --release

# Run the smoke test.
uv run pytest -q
```

## License

BSD-2-Clause. See [`LICENSE`](LICENSE).
