"""Fast fuzzy parsing of dates in varying formats.

The Rust extension exposes a single function, :func:`parse`, that takes a
list of raw date strings and returns a JSON-encoded array of ISO-8601
representations. Inputs that fail to parse produce ``null`` in the
corresponding slot.

Example:
    >>> import json
    >>> import date_parser
    >>> json.loads(date_parser.parse(["2026-01-01", "garbage", "06/15/2024"]))
    ['2026-01-01 00:00:00+00:00', None, '2024-06-15 00:00:00+00:00']
"""

from .date_parser import parse

__all__ = ["parse"]
__version__ = "0.1.0"
