"""Tests for writer path-resolution safety."""

from pathlib import Path

import pytest

from src import writer


@pytest.fixture
def fake_vault(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "Daily Log").mkdir()
    (vault / "Daily Log" / "2026-05-12.md").write_text("# today\n")
    monkeypatch.setattr(writer, "VAULT_PATH", vault)
    return vault


def test_resolve_accepts_vault_relative(fake_vault):
    resolved = writer._resolve("Daily Log/2026-05-12.md")
    assert resolved is not None
    assert resolved == (fake_vault / "Daily Log" / "2026-05-12.md").resolve()


def test_resolve_rejects_traversal_escape(fake_vault):
    assert writer._resolve("../../etc/passwd") is None


def test_resolve_rejects_absolute_path(fake_vault):
    assert writer._resolve("/etc/passwd") is None
    # Even absolute paths INSIDE the vault are rejected — API takes vault-relative only.
    assert writer._resolve(str(fake_vault / "Daily Log" / "2026-05-12.md")) is None


def test_resolve_rejects_tilde_expansion(fake_vault):
    assert writer._resolve("~/something.md") is None


def test_resolve_rejects_empty(fake_vault):
    assert writer._resolve("") is None
