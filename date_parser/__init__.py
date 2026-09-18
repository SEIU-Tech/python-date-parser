"""Fast fuzzy parsing of dates in varying formats.

The Rust extension exposes three functions:

- :func:`parse` takes a single raw date string and returns its ISO-8601
  representation (or ``None`` for inputs the parser can't handle).
- :func:`parse_list` takes a Python list of raw date strings and returns
  a list of ISO-8601 strings (or ``None`` for inputs that fail to parse
  in the corresponding slot). The returned list matches the input
  length and order.
- :func:`parse_series` takes a Polars ``Series`` of string dtype and
  returns a Polars ``Series`` of ``pl.Datetime("ns")``. Unparseable
  strings become null values in the result. The Polars path works on
  the underlying Arrow buffer directly via ``pyo3-polars``, so there is
  no Python-level iteration.

Example:
    >>> import polars as pl, date_parser
    >>> date_parser.parse("2026-01-01")
    '2026-01-01 00:00:00+00:00'
    >>> date_parser.parse("garbage") is None
    True
    >>> date_parser.parse_list(["2026-01-01", "garbage", "06/15/2024"])
    ['2026-01-01 00:00:00+00:00', None, '2024-06-15 00:00:00+00:00']
    >>> date_parser.parse_series(pl.Series(["2026-01-01", "garbage"])).dtype
    Datetime('ns')
"""

from .date_parser import parse, parse_list, parse_series

__all__ = ["parse", "parse_list", "parse_series"]
__version__ = "0.1.0"
