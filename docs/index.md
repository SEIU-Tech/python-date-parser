# python-date-parser

Fast fuzzy parsing of dates in varying formats.

## Status

This repository currently contains only the build scaffolding — the actual
date-parsing logic has not yet been implemented. The Rust extension exposes
a single placeholder function (`parse_date`) that round-trips its input,
just enough to verify the build pipeline end-to-end.

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
