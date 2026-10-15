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

Polars is an *optional* dependency: without it installed, :func:`parse`
and :func:`parse_list` work unchanged, and calling :func:`parse_series`
raises an ``ImportError`` pointing to the ``[polars]`` extra.

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

from importlib.util import find_spec

from .date_parser import parse, parse_list

if find_spec("polars") is None:
    def parse_series(series: object) -> object:
        """Raise an informative error: Polars is not installed.

        The real ``parse_series`` lives in the Rust extension; this stub
        only takes its place when the optional Polars dependency is
        missing, so callers get actionable guidance rather than a bare
        ``ModuleNotFoundError`` from deep inside Polars itself.
        """
        raise ImportError(
            "date_parser.parse_series() requires Polars, which is an "
            "optional dependency of gnosis-date-parser.\n"
            "Install it with:  pip install gnosis-date-parser[polars]"
        ) from None

else:
    from .date_parser import parse_series  # type: ignore[no-redef]

__all__ = ["parse", "parse_list", "parse_series"]
__version__ = "1.1.0"
