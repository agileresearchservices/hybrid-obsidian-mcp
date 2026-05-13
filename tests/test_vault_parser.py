"""Tests for vault_parser helpers — specifically the frontmatter date guard.

The OpenSearch `date` field uses strict format `yyyy-MM-dd`, so anything that
matches the regex but isn't a real calendar date (e.g. `1031-20-25`,
`9999-99-99`) crashes the bulk index. normalize_date validates with strptime
and returns None for those so the chunk indexes without a date field.
"""

from datetime import datetime
from pathlib import Path

from src.vault_parser import normalize_date, parse_note


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


def _write(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


def test_parse_note_short_body_with_tags_indexes_synthetic_chunk(tmp_path):
    """Short-body notes (e.g. credential stubs) stay searchable via frontmatter."""
    body = (
        "---\n"
        "title: Okta Preview Password\n"
        "tags: [secrets, credentials, nasuni]\n"
        "---\n"
        "\n"
        "abc123\n"
    )
    f = _write(tmp_path, "Okta Preview Password.md", body)
    note = parse_note(f, tmp_path)
    assert note is not None
    assert len(note.chunks) == 1
    chunk = note.chunks[0]
    assert "Okta Preview Password" in chunk
    assert "secrets" in chunk and "credentials" in chunk
    assert "abc123" in chunk


def test_parse_note_truly_empty_returns_none(tmp_path):
    """A file with no body and no tags has nothing to index."""
    f = _write(tmp_path, "empty.md", "---\ntitle: Empty\n---\n")
    assert parse_note(f, tmp_path) is None


def test_parse_note_long_body_uses_normal_chunking(tmp_path):
    """Regression: notes with substantive bodies still go through chunk_text."""
    body = "---\ntitle: Long\ntags: [foo]\n---\n\n" + ("Lorem ipsum dolor sit amet. " * 20)
    f = _write(tmp_path, "long.md", body)
    note = parse_note(f, tmp_path)
    assert note is not None
    assert len(note.chunks) >= 1
    # synthetic-chunk marker would force a "Folder:" header; normal path won't
    assert not note.chunks[0].startswith("Long\nTags:")
