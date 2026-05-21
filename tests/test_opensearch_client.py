"""Tests for index settings and ensure_index behavior."""

from unittest.mock import MagicMock

from src import opensearch_client


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
