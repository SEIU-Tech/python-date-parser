import json

import date_parser


def test_parse_iso_date() -> None:
    """A canonical ISO-8601 date should round-trip cleanly."""
    assert json.loads(date_parser.parse(["2026-09-18"])) == [
        "2026-09-18 00:00:00+00:00"
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
