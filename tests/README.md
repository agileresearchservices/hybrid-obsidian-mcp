# Tests

`pytest` suite over the core modules. Tests are designed to run without OpenSearch or Ollama — those layers are mocked.

```bash
uv run pytest tests/                    # full suite
uv run pytest tests/test_searcher_filters.py -v
uv run pytest -k "exclude_tags"         # name-filter
```

## File map

| File | Covers |
|---|---|
| `test_embeddings.py` | Ollama client, tenacity retry, batched array input, `get_embedding` LRU. |
| `test_embed_cache.py` | `chunk_hash`-based vector reuse on incremental index. Verifies `cache_hits` / `cache_misses` accounting. |
| `test_writer_paths.py` | Path safety contract — absolute paths and `~`-prefixed paths are rejected by every write tool. |
| `test_tagger.py` | Bulk-tag pipeline: normalization, aliases, blocklist, dry-run, preflight validation, consolidation thresholds. |
| `test_searcher_filters.py` | Tag / folder / date filters, `exclude_tags` on both hybrid and RRF paths. |
| `test_reranker.py` | Cross-encoder lazy load, score cache, only-missing-pairs forwarded to the model. |
| `test_opensearch_client.py` | Client singleton (`reset_client` for tests), `ensure_index()` settings sync via `put_settings`. |
| `test_server.py` | MCP tool wiring — every `@mcp.tool` is callable and returns the documented shape. |
| `test_vault_parser.py` | YAML frontmatter parsing, section-aware chunking, tag extraction, `chunk_hash` stability. |
| `test_cache_stats.py` | Unified snapshot across the four in-process caches; hit-rate math; `null` vs `0` semantics. |

## Patterns to follow when adding a test

- **Mock at the boundary, not the function under test.** OpenSearch and Ollama calls have dedicated mock helpers — reuse them rather than re-mocking inline.
- **No real network.** Tests must run with the network unplugged and `localhost:9201` / `localhost:11434` unreachable.
- **One assertion per behaviour.** When a test fails, the failure should name the broken behaviour, not require diff archaeology.
- **Fixtures over `setUp`.** Use pytest fixtures; the conftest-style sharing of vault paths and mock clients is already there.

## Common gotcha

If a test starts hitting real OpenSearch unexpectedly, check that you didn't import a top-level `from src.opensearch_client import client` — that triggers the singleton at import time. Call `create_client()` inside the test or rely on the existing fixture.
