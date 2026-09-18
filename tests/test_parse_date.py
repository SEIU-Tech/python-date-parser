from date_parser import parse_date


def test_parse_date_placeholder() -> None:
    """Placeholder round-trip; replaced when real parsing lands."""
    assert parse_date("2026-09-18") == "2026-09-18"
