import json
from datetime import datetime

import polars as pl

import date_parser


def test_parse_iso_date() -> None:
    """A canonical ISO-8601 date should round-trip cleanly."""
    assert json.loads(date_parser.parse(["2026-09-18"])) == [
        "2026-09-18 00:00:00+00:00"
    ]


def test_parse_iso8601_t_separator() -> None:
    """ISO-8601 datetimes using the ``T`` separator should parse correctly.

    The underlying ``dateparser`` crate rejects the no-offset form
    ``YYYY-MM-DDTHH:MM:SS``; we normalize the separator to a space.
    The forms that already included a timezone designator (``Z`` or
    ``±HH:MM``) are regression checks.
    """
    # No offset — the case the crate refuses, fixed by our normalizer.
    assert json.loads(date_parser.parse(["2026-09-18T01:02:03"])) == [
        "2026-09-18 01:02:03+00:00"
    ]
    # No offset, with fractional seconds.
    assert json.loads(date_parser.parse(["2026-09-18T01:02:03.123"])) == [
        "2026-09-18 01:02:03.123+00:00"
    ]
    # With ``Z`` (already worked before, included as a regression check).
    assert json.loads(date_parser.parse(["2026-09-18T01:02:03Z"])) == [
        "2026-09-18 01:02:03+00:00"
    ]
    # With explicit offset (also previously working, kept as regression).
    assert json.loads(date_parser.parse(["2026-09-18T01:02:03+05:30"])) == [
        "2026-09-17 19:32:03+00:00"
    ]


def test_parse_multiple() -> None:
    """Each input is parsed independently and results are returned in order."""
    assert json.loads(
        date_parser.parse(["2026-01-01", "2026-02-02", "2026-03-03"])
    ) == [
        "2026-01-01 00:00:00+00:00",
        "2026-02-02 00:00:00+00:00",
        "2026-03-03 00:00:00+00:00",
    ]


def test_parse_unparseable_returns_null() -> None:
    """Inputs that the parser cannot handle become ``null`` in the output."""
    assert json.loads(date_parser.parse(["not a date", "garbage"])) == [None, None]


def test_parse_mixed() -> None:
    """Successful and unsuccessful parses can be interleaved."""
    result = json.loads(date_parser.parse(["2026-01-01", "garbage", "2026-02-02"]))
    assert result == [
        "2026-01-01 00:00:00+00:00",
        None,
        "2026-02-02 00:00:00+00:00",
    ]


def test_parse_empty_list() -> None:
    """An empty input list yields an empty JSON array."""
    assert date_parser.parse([]) == "[]"


def test_parse_timestamp() -> None:
    """Unix timestamps are interpreted as UTC.

    A 10-digit value is seconds, 13 is milliseconds, 16 microseconds and 19
    nanoseconds since the Unix epoch. Trailing zeros in the fractional
    component are trimmed, so 429 ms renders as ``.429``.
    """
    # 1511648546 seconds -> 2017-11-25 22:22:26 UTC.
    assert json.loads(date_parser.parse(["1511648546"])) == [
        "2017-11-25 22:22:26+00:00"
    ]
    # 1620021848429 ms -> 2021-05-03 06:04:08.429 UTC.
    assert json.loads(date_parser.parse(["1620021848429"])) == [
        "2021-05-03 06:04:08.429+00:00"
    ]
    # 1620024872717915000 ns -> 2021-05-03 06:54:32.717915 UTC.
    assert json.loads(date_parser.parse(["1620024872717915000"])) == [
        "2021-05-03 06:54:32.717915+00:00"
    ]


def test_parse_series_basic() -> None:
    """A string Series should parse into a Datetime Series with the right dtype."""
    s = pl.Series(["2026-09-18", "garbage", "2026-09-18T01:02:03", "1511648546"])
    result = date_parser.parse_series(s)
    assert result.dtype == pl.Datetime("ns")
    assert result.to_list() == [
        datetime(2026, 9, 18, 0, 0, 0),
        None,
        datetime(2026, 9, 18, 1, 2, 3),
        datetime(2017, 11, 25, 22, 22, 26),
    ]
    assert result.null_count() == 1


def test_parse_series_empty() -> None:
    """An empty Series should produce an empty Datetime Series."""
    s = pl.Series([], dtype=pl.String)
    result = date_parser.parse_series(s)
    assert result.dtype == pl.Datetime("ns")
    assert len(result) == 0


def test_parse_series_all_parseable() -> None:
    """A Series of all-parseable strings should yield no nulls."""
    s = pl.Series(["2026-09-18", "2026-09-18 01:02:03", "2026-09-18T01:02:03Z"])
    result = date_parser.parse_series(s)
    assert result.dtype == pl.Datetime("ns")
    assert result.null_count() == 0
    assert len(result) == 3


def test_parse_series_all_null_input() -> None:
    """Nulls in the input propagate as nulls in the output."""
    s = pl.Series([None, None, None], dtype=pl.String)
    result = date_parser.parse_series(s)
    assert result.dtype == pl.Datetime("ns")
    assert result.null_count() == 3


def test_parse_series_matches_parse() -> None:
    """The Series and list APIs should agree on every input.

    This is the cross-check: ``parse`` produces ISO-8601 strings and
    ``parse_series`` produces Arrow datetimes; converting each through
    a common reference must yield identical instants for every row.
    """
    raw = [
        "2026-09-18",
        "2026-09-18 01:02:03",
        "2026-09-18T01:02:03",
        "2026-09-18T01:02:03Z",
        "2026-09-18T01:02:03+05:30",
        "2026-09-18 13:31:15 PST",
        "garbage",
    ]
    list_results = json.loads(date_parser.parse(raw))
    series_result = date_parser.parse_series(pl.Series(raw))

    for raw_input, list_str, dt in zip(
        raw, list_results, series_result.to_list(), strict=False
    ):
        if list_str is None:
            assert dt is None, f"parse() returned None for {raw_input!r} but parse_series did not"
            continue
        # ``parse()`` emits an aware ISO-8601 string (`+00:00`); the
        # ``Datetime("ns")`` Series is naive. Both encode the same UTC
        # instant — compare by replacing the tzinfo with ``None``.
        expected = datetime.fromisoformat(list_str).replace(tzinfo=None)
        assert dt == expected, (
            f"mismatch for {raw_input!r}: parse() -> {list_str!r}, "
            f"parse_series -> {dt!r}"
        )
