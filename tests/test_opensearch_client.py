"""Tests for index settings, ensure_index, and client lifecycle."""

from unittest.mock import MagicMock

import pytest

from src import opensearch_client


@pytest.fixture(autouse=True)
def _reset_client_singleton():
    opensearch_client.reset_client()
    yield
    opensearch_client.reset_client()


def test_index_mapping_includes_refresh_interval():
    """Settings dict must carry refresh_interval so new indexes pick it up."""
    interval = opensearch_client.INDEX_MAPPING["settings"]["index"]["refresh_interval"]
    assert interval == opensearch_client.OPENSEARCH_REFRESH_INTERVAL


def test_ensure_index_applies_refresh_interval_to_existing_index(monkeypatch):
    """If the index already exists, put_settings must sync refresh_interval."""
    client = MagicMock()
    client.indices.exists.return_value = True

    opensearch_client.ensure_index(client)

    client.indices.create.assert_not_called()
    # put_settings must be called with the configured refresh_interval
    client.indices.put_settings.assert_called_once()
    call = client.indices.put_settings.call_args
    body = call.kwargs.get("body")
    if body is None and call.args:
        body = call.args[-1]
    assert body is not None, f"put_settings called without a body: args={call.args}, kwargs={call.kwargs}"
    assert body["index"]["refresh_interval"] == opensearch_client.OPENSEARCH_REFRESH_INTERVAL


def test_ensure_index_creates_with_mapping_when_missing():
    """Fresh index creation passes the full mapping (which includes refresh_interval)."""
    client = MagicMock()
    client.indices.exists.return_value = False

    opensearch_client.ensure_index(client)

    client.indices.create.assert_called_once()
    create_body = client.indices.create.call_args.kwargs["body"]
    assert create_body["settings"]["index"]["refresh_interval"] == opensearch_client.OPENSEARCH_REFRESH_INTERVAL


def test_ensure_index_swallows_put_settings_errors():
    """A failing put_settings must not break ensure_index for the existing-index path."""
    client = MagicMock()
    client.indices.exists.return_value = True
    client.indices.put_settings.side_effect = RuntimeError("boom")

    # Should not raise
    opensearch_client.ensure_index(client)


# ---------------------------------------------------------------------------
# Client singleton
# ---------------------------------------------------------------------------


def test_create_client_returns_same_instance_across_calls(monkeypatch):
    """Calling create_client() N times must return the same object."""
    constructor = MagicMock(return_value=MagicMock(name="opensearch-client"))
    monkeypatch.setattr(opensearch_client, "OpenSearch", constructor)

    a = opensearch_client.create_client()
    b = opensearch_client.create_client()
    c = opensearch_client.create_client()

    assert a is b is c
    assert constructor.call_count == 1


def test_reset_client_forces_fresh_instantiation(monkeypatch):
    constructor = MagicMock(side_effect=lambda **kw: MagicMock(name="client"))
    monkeypatch.setattr(opensearch_client, "OpenSearch", constructor)

    a = opensearch_client.create_client()
    opensearch_client.reset_client()
    b = opensearch_client.create_client()

    assert a is not b
    assert constructor.call_count == 2


def test_create_client_passes_expected_kwargs(monkeypatch):
    constructor = MagicMock(return_value=MagicMock())
    monkeypatch.setattr(opensearch_client, "OpenSearch", constructor)

    opensearch_client.create_client()

    constructor.assert_called_once()
    kwargs = constructor.call_args.kwargs
    assert kwargs["hosts"] == [
        {"host": opensearch_client.OPENSEARCH_HOST, "port": opensearch_client.OPENSEARCH_PORT}
    ]
    assert kwargs["retry_on_timeout"] is True
    assert kwargs["max_retries"] == 3
