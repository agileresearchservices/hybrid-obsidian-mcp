"""Tests for vault_parser helpers — specifically the frontmatter date guard.

The OpenSearch `date` field uses strict format `yyyy-MM-dd`, so anything that
matches the regex but isn't a real calendar date (e.g. `1031-20-25`,
`9999-99-99`) crashes the bulk index. normalize_date validates with strptime
and returns None for those so the chunk indexes without a date field.
"""

from datetime import datetime

from src.vault_parser import normalize_date


def test_normalize_date_accepts_valid_iso_date():
    assert normalize_date("2026-05-12") == "2026-05-12"


def test_normalize_date_extracts_prefix_from_iso_datetime():
    assert normalize_date("2026-05-12T14:30:00.000Z") == "2026-05-12"


def test_normalize_date_accepts_datetime_object():
    assert normalize_date(datetime(2026, 5, 12)) == "2026-05-12"


def test_normalize_date_none_returns_none():
    assert normalize_date(None) is None


def test_normalize_date_unmatched_string_returns_none():
    assert normalize_date("not a date") is None
    assert normalize_date("05/12/2026") is None


def test_normalize_date_rejects_month_out_of_range():
    # Real frontmatter typo we hit: "10-31-2025" written as "1031-20-25"
    assert normalize_date("1031-20-25") is None


def test_normalize_date_rejects_day_out_of_range():
    assert normalize_date("2026-02-30") is None


def test_normalize_date_rejects_all_nines_sentinel():
    # Seen in a Thermo Code Reference note as a "missing data" placeholder.
    assert normalize_date("9999-99-99") is None


def test_normalize_date_rejects_zero_month():
    assert normalize_date("2026-00-15") is None
