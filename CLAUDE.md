# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Start OpenSearch
docker compose up -d

# Install dependencies
uv sync

# Run MCP server
uv run obsidian-mcp
# or: python -m src.server

# Run vault file watcher
uv run obsidian-watcher
# or: python -m src.watcher

# Shell CLI (same Python as the MCP server — used by slack-gateway, cron, etc.)
uv run obsidian-cli <subcommand>
# e.g. obsidian-cli list-todos, obsidian-cli daily-log view, obsidian-cli taxonomy

# Full vault reindex
python -m src.indexer

# Incremental index specific files
python -m src.indexer --files "Daily Log/2026-04-08.md" "KMW/Notes.md"

# Search from CLI
python -m src.searcher "your query"
```

## Architecture

This is a **FastMCP server** providing hybrid search and vault management over an Obsidian vault, backed by OpenSearch and Ollama.

**Data flow for search:**
1. Query → `src/searcher.py` → Ollama embeddings + OpenSearch hybrid query (kNN + BM25)
2. Top-K results → `src/reranker.py` (cross-encoder ms-marco-MiniLM-L-6-v2) → reranked results

**Data flow for indexing:**
1. Vault `.md` files → `src/vault_parser.py` (frontmatter, chunking, tag/link extraction)
2. Chunks → `src/embeddings.py` batches them to Ollama → OpenSearch bulk index

**Module responsibilities:**
- `src/server.py` — FastMCP tool definitions (search, index, todos, daily logs, notes, bulk-tag, cache_stats). `_prewarm_reranker_if_enabled()` runs at startup so the first search query doesn't pay the ~4s cross-encoder load; gated behind `RERANKER_PREWARM` (default `true`). Failures are logged and swallowed — the on-demand load path remains the fallback
- `src/cache_stats.py` — single aggregator over the four in-process caches; surfaces `hits/misses/sizes/hit_rate` via the `cache_stats` MCP tool and `obsidian-cli cache-stats`
- `src/cli.py` — `obsidian-cli` shell entrypoint; same codepath as MCP tools. Used by slack-gateway, cron jobs, and any other automation.
- `src/searcher.py` — Hybrid search (kNN + BM25), keyword search, list/filter by metadata
- `src/indexer.py` — Full reindex, incremental `index_files()` (with chunk-level embed cache via `chunk_hash`), and `delete_files()` (stale-chunk cleanup by `file_path`)
- `src/embeddings.py` — Shared Ollama client with tenacity retry + array-input batching. Both indexer (`search_document:` prefix) and searcher (`search_query:` prefix) call through here. `get_embedding()` is memoized via `functools.lru_cache` keyed on `(task, text)`; size controlled by `EMBEDDING_QUERY_CACHE_SIZE` (default 256, set to 0 to disable). Cleared on process restart
- `src/writer.py` — Vault write operations: todos, daily logs, note create/append. Paths must be vault-relative; absolute or `~`-prefixed paths are rejected
- `src/tagger.py` — Bulk tag operations: taxonomy collection, frontmatter merges, workflow prompt. `bulk_apply` pre-validates every path before any write and supports `dry_run`. `collect_taxonomy()` is memoized for `TAXONOMY_CACHE_TTL_SECONDS` (default 60s, 0 disables) so a bulk-tag workflow doesn't rescan the vault 3-4× per run. `read_note(rel_path)` is LRU-cached keyed on `(resolved_path, mtime_ns)` (size `READ_NOTE_CACHE_SIZE`, default 64) — edits auto-invalidate because mtime changes the key. `clear_taxonomy_cache()` / `taxonomy_cache_info()` / `clear_read_note_cache()` / `read_note_cache_info()` for ops
- `src/vault_parser.py` — YAML frontmatter, section-aware chunking, tag extraction
- `src/opensearch_client.py` — Client setup, index mapping (768-dim HNSW), hybrid search pipeline. `create_client()` is a process-wide singleton so callers share one transport pool and keep-alive connections survive between queries; `reset_client()` is for tests. `index.refresh_interval` controlled by `OPENSEARCH_REFRESH_INTERVAL` (default `5s`); `ensure_index` syncs the setting to existing indexes via `put_settings` so config changes propagate without recreating the index
- `src/reranker.py` — Lazy-loaded cross-encoder singleton; disabled via `ENABLE_RERANKING=false`. Per-pair score cache keyed on `(sha256(query), chunk_hash)`; size controlled by `RERANKER_CACHE_SIZE` (default 1024, 0 disables). Misses fall through to `CrossEncoder.predict()`; only the missing pairs are passed to the model
- `src/watcher.py` — watchdog file watcher with 10s debounce; handles modify/create/move/delete and routes to `index_files()` / `delete_files()`
- `src/config.py` — All config from `.env` with defaults

**Indexing performance:** `_prepare_note_docs()` builds chunk docs without embeddings, then `_embed_and_extend()` calls `get_embeddings_batch()` once per ~50-chunk window. This collapses what was previously one HTTP call per chunk into one call per batch — full reindex drops from ~1 hour to a few minutes.

**Embed cache (incremental only):** every chunk doc stores `chunk_hash = sha256(embed_input)`. `index_files()` loads `{chunk_hash: embedding}` for the affected paths *before* `delete_by_query`, and `_embed_and_extend()` reuses cached vectors for matching hashes — so editing one paragraph in a long note re-embeds only the touched chunks. Stats are returned as `cache_hits` / `cache_misses`. Tag order is sorted in `parse_note` to keep the hash stable across runs. Full reindex (`index_vault`) bypasses the cache.

**Tests:** `tests/test_embeddings.py`, `tests/test_embed_cache.py`, and `tests/test_writer_paths.py`. Run with `uv run pytest tests/`.

**Single source of truth**: this project owns every Obsidian vault operation. The former `~/.claude/skills/obsidian/` skill has been deleted; slack-gateway and cron jobs now call `obsidian-cli` directly.

**Bulk tag workflow**: `mcp__obsidian-search__bulk_tag_workflow()` returns an orchestration prompt. The flow dispatches `general-purpose` subagents with `model: "haiku"` to propose tags per-note in parallel batches, then applies via `bulk_tag_apply`. All classification LLM work uses Haiku for cost efficiency.

## External Services

| Service | Purpose | Default |
|---|---|---|
| OpenSearch 3.5.0 | Search index (docker-compose) | `localhost:9201` |
| Ollama | Embeddings (`nomic-embed-text`, 768 dims) | `localhost:11434` |

## Key Configuration (`.env`)

```
OBSIDIAN_VAULT_PATH=~/Library/Mobile Documents/iCloud~md~obsidian/Documents/obsidian-vault
OPENSEARCH_HOST=localhost
OPENSEARCH_REFRESH_INTERVAL=5s  # index refresh cadence; default 5s (was OpenSearch default 1s)
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_EMBED_MODEL=nomic-embed-text
EMBEDDING_QUERY_CACHE_SIZE=256  # LRU for single-text query embeddings; 0 = disabled
VECTOR_WEIGHT=0.3       # weight in hybrid score
LEXICAL_WEIGHT=0.7
RETRIEVER_K=10          # results returned
RETRIEVER_FETCH_K=40    # candidates before reranking
ENABLE_RERANKING=true
RERANKER_PREWARM=true    # Load cross-encoder at MCP startup; false for fast dev iteration
RERANKER_CACHE_SIZE=1024 # LRU of (query, chunk_hash) -> score; 0 = disabled
TAXONOMY_CACHE_TTL_SECONDS=60 # TTL for collect_taxonomy(); 0 = disabled
READ_NOTE_CACHE_SIZE=64       # LRU for read_note() keyed on (path, mtime_ns); 0 = disabled
RECENCY_DECAY_ENABLED=true
RECENCY_DECAY_SCALE=90d
RECENCY_DECAY_WEIGHT=0.3
```

Recency decay applies a `gauss(file_mtime)` function score to the BM25 sub-query of `hybrid_search` (and to the BM25 side of the RRF fallback). The hybrid search-pipeline's min-max normalization compresses absolute score magnitudes, so the decay influences in-window ordering rather than dominating the final score — tune `WEIGHT`/`SCALE` if you want a stronger pull. `exclude_tags` (comma-separated for the MCP tool / CLI) is a `must_not` filter on `tags.keyword` and is honored by both the hybrid path and the RRF fallback.

## OpenSearch Index Design

- Index: `obsidian_notes`
- `embedding`: knn_vector (768 dims, HNSW, cosinesimil)
- `chunk_text`: text with English analyzer
- `tags`, `folder`, `doc_type`, `file_path`: keyword (filterable)
- `date`: date field (YYYY-MM-DD)
- Search pipeline `obsidian_hybrid_pipeline`: min-max normalization + weighted arithmetic mean

## Deployment

- **macOS launchd daemon**: `com.obsidian.search-watcher.plist` keeps the watcher running at startup; logs go to `logs/`
- The watcher auto-indexes `.md` file changes with a 10s debounce — manual `index_notes` is rarely needed

## Expert Skills

Use `/opensearch-expert` for OpenSearch Query DSL, search relevancy, or mapping changes.
