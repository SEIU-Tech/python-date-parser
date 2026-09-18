# python-date-parser

Fast fuzzy parsing of dates in varying formats.

## Status

The Rust extension parses raw date strings into ISO-8601 representations
using the [`dateparser`](https://docs.rs/dateparser/0.3.1/dateparser/)
crate. The single public function is `parse(raw_dates: list[str]) -> str`:

```python
>>> import json, date_parser
>>> json.loads(date_parser.parse(["2026-01-01", "garbage", "06/15/2024"]))
['2026-01-01 00:00:00+00:00', None, '2024-06-15 00:00:00+00:00']
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
