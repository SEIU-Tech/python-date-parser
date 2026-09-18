# python-date-parser

Perform fast fuzzy parsing of dates in varying formats.

## Status

Initial scaffold only. The Rust extension currently exposes a single
placeholder function (`parse_date`) so the build pipeline can be verified
end-to-end. Real date-parsing logic has not yet been implemented.

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
