# Architecture

A short tour of how a query becomes ranked results, and how a vault edit becomes new vectors.

## System diagram

```text
                              ┌────────────────────┐
                              │  Obsidian vault    │
                              │  (.md files)       │
                              └──────────┬─────────┘
                                         │ watchdog (10s debounce)
                                         ▼
   ┌──────────────────┐         ┌────────────────────┐         ┌────────────────────┐
   │  MCP client      │         │  obsidian-watcher  │         │  obsidian-cli      │
   │  (Claude Code,   │         │  (file events →    │         │  (cron, scripts,   │
   │   IDEs, agents)  │         │   index_files)     │         │   slack-gateway)   │
   └────────┬─────────┘         └─────────┬──────────┘         └──────────┬─────────┘
            │ stdio                       │                                │
            ▼                             ▼                                ▼
   ┌─────────────────────────────────────────────────────────────────────────────┐
   │                          src/ — shared Python codepath                      │
   │  searcher · indexer · writer · tagger · reranker · embeddings · vault_parser│
   └────────────────────┬──────────────────────────────────┬─────────────────────┘
                        │                                  │
                        ▼                                  ▼
              ┌──────────────────┐               ┌────────────────────┐
              │ Ollama           │               │ OpenSearch 3.5     │
              │ nomic-embed-text │               │ obsidian_notes idx │
              │ (768-dim)        │               │ HNSW + BM25 +      │
              └──────────────────┘               │ hybrid pipeline    │
                                                 └────────────────────┘
```

The MCP server (`obsidian-mcp`), the watcher (`obsidian-watcher`), and the CLI (`obsidian-cli`) are three thin entrypoints over the same `src/` package — there's no API drift between them.

## Search data flow

1. **Query embedding.** `searcher.hybrid_search` calls `embeddings.get_embedding(query, task="search_query")`. Single-text embeddings are memoized via `functools.lru_cache` keyed on `(task, text)` — repeat queries are free.
2. **Hybrid OpenSearch query.** The query is sent as a compound: a kNN clause over the `embedding` field (top-`FETCH_K` candidates) plus a `multi_match` BM25 clause over `chunk_text`. Recency decay (if enabled) wraps the BM25 side in a `function_score` `gauss(file_mtime)`.
3. **Score fusion.** OpenSearch's `obsidian_hybrid_pipeline` runs each sub-query's hits through min-max normalization, then takes a weighted arithmetic mean — `VECTOR_WEIGHT * norm_knn + LEXICAL_WEIGHT * norm_bm25`.
4. **Reranking.** The top-`K` are scored by the `ms-marco-MiniLM-L-6-v2` cross-encoder. Scores are cached per `(sha256(query), chunk_hash)` so unchanged chunks don't re-run the model.
5. **Return.** Ranked `SearchResult` objects with chunk text + metadata (`title`, `date`, `tags`, `folder`, `file_path`).

If the search pipeline fails (older OpenSearch, missing pipeline, etc.), `searcher` falls back to a manual **RRF (reciprocal rank fusion)** path that runs the kNN and BM25 queries separately and fuses them in Python.

## Indexing data flow

### Full reindex (`python -m src.indexer`)

1. `vault_parser.parse_vault()` walks the vault, parses YAML frontmatter, chunks each note by section (max ~1000 chars), and extracts tags.
2. `_prepare_note_docs()` builds chunk docs *without* embeddings.
3. `_embed_and_extend()` batches ~50 chunks at a time to Ollama via `embeddings.get_embeddings_batch()`, prefixed with `search_document:`.
4. Bulk-index into `obsidian_notes` with `file_mtime`, `chunk_hash`, and all metadata.

This batched flow turned a one-HTTP-call-per-chunk run (~1 hour) into a few minutes.

### Incremental reindex (watcher / `index_notes` MCP tool / `--files`)

1. Load `{chunk_hash: embedding}` from OpenSearch for the affected paths.
2. `delete_by_query` the existing chunks for those paths.
3. Parse the files; for each new chunk, if its `chunk_hash` matches a cached vector, reuse it; otherwise embed.
4. Bulk-index. Return `{indexed, chunks, cache_hits, cache_misses}`.

The hash is `sha256(embed_input)`, where `embed_input` includes sorted tags (so re-ordering frontmatter doesn't invalidate). The practical effect: editing one paragraph in a 50-section note re-embeds only that paragraph's chunks.

## Module map

| Module | Responsibility |
|---|---|
| `src/server.py` | FastMCP tool definitions. Prewarms the cross-encoder at startup (gated by `RERANKER_PREWARM`). |
| `src/cli.py` | `obsidian-cli` subcommands. Same Python codepath as MCP tools. |
| `src/searcher.py` | Hybrid + keyword search, list-by-metadata, RRF fallback. |
| `src/indexer.py` | Full reindex (`index_vault`), incremental (`index_files`), and stale-chunk cleanup (`delete_files`). |
| `src/writer.py` | Vault writes — notes, todos, daily logs. Vault-relative paths only; absolute and `~`-prefixed paths are rejected. |
| `src/tagger.py` | Bulk-tag workflow: taxonomy collection, batch prep, frontmatter merges, consolidation. `read_note()` is LRU-cached keyed on `(path, mtime_ns)`. |
| `src/vault_parser.py` | YAML frontmatter, section-aware chunking, tag extraction. |
| `src/embeddings.py` | Ollama client with tenacity retry, batched array input. `get_embedding()` is `lru_cache`-memoized. |
| `src/reranker.py` | Lazy-loaded cross-encoder singleton with per-pair score cache. |
| `src/opensearch_client.py` | Process-wide singleton client, index mapping (768-dim HNSW + keyword filters), hybrid search pipeline. |
| `src/watcher.py` | `watchdog` observer with 10s debounce. Routes events to `index_files()` / `delete_files()`. |
| `src/cache_stats.py` | Single aggregator over the four in-process caches; powers `cache_stats` MCP tool and `obsidian-cli cache-stats`. |
| `src/config.py` | All configuration sourced from `.env` with defaults. |

## OpenSearch index

**Index:** `obsidian_notes`

| Field | Type | Use |
|---|---|---|
| `embedding` | `knn_vector` (768 dims, HNSW, cosinesimil) | Semantic search |
| `chunk_text` | `text` (English analyzer) | BM25 full-text |
| `chunk_hash` | `keyword` | Embed cache key — `sha256(embed_input)` |
| `tags` | `keyword` | Tag filters / faceting |
| `folder` | `keyword` | Folder filters |
| `doc_type` | `keyword` | `daily-log`, `todo`, etc. |
| `file_path` | `keyword` | Path-based filters and stale-chunk deletes |
| `file_mtime` | `date` | Recency decay |
| `date` | `date` (`yyyy-MM-dd`) | Date filtering from frontmatter |

**Search pipeline:** `obsidian_hybrid_pipeline` — min-max normalization of each sub-query's scores, then weighted arithmetic mean.

**Refresh interval:** `OPENSEARCH_REFRESH_INTERVAL` (default `5s`, vs OpenSearch's `1s` default). The 10s debounce in the watcher already caps how fast new docs become visible, so a 5s refresh cuts segment-flush overhead with no user-visible lag. `ensure_index()` syncs this setting to existing indexes via `put_settings` so config changes propagate without recreating the index.

## In-process caches

All four are per-process and cleared on restart. The `cache_stats` MCP tool and `obsidian-cli cache-stats` give a single JSON snapshot of hits/misses/sizes/hit_rate.

| Cache | Key | Size knob |
|---|---|---|
| Embedding query | `(task, text)` | `EMBEDDING_QUERY_CACHE_SIZE` (default 256) |
| Reranker scores | `(sha256(query), chunk_hash)` | `RERANKER_CACHE_SIZE` (default 1024) |
| Taxonomy | n/a (TTL) | `TAXONOMY_CACHE_TTL_SECONDS` (default 60s) |
| `read_note` | `(resolved_path, mtime_ns)` | `READ_NOTE_CACHE_SIZE` (default 64) |

Edits auto-invalidate the `read_note` cache because `mtime_ns` is part of the key. Set any size to `0` to disable the corresponding cache.
