# Configuration

All configuration is loaded from `.env` in the project root, with defaults defined in `src/config.py`. Restart the MCP server and the watcher after edits.

## Vault

| Variable | Default | Description |
|---|---|---|
| `OBSIDIAN_VAULT_PATH` | `~/Library/Mobile Documents/iCloud~md~obsidian/Documents/obsidian-vault` | Absolute path to the vault. The macOS iCloud default works out of the box; otherwise set it. |

## OpenSearch

| Variable | Default | Description |
|---|---|---|
| `OPENSEARCH_HOST` | `localhost` | Hostname. |
| `OPENSEARCH_PORT` | `9201` | Port. |
| `OPENSEARCH_INDEX_NAME` | `obsidian_notes` | Index. |
| `OPENSEARCH_SEARCH_PIPELINE` | `obsidian_hybrid_pipeline` | Min-max normalize + weighted arithmetic mean. Created automatically. |
| `OPENSEARCH_TIMEOUT` | `30` | Request timeout, seconds. |
| `OPENSEARCH_REFRESH_INTERVAL` | `5s` | Index refresh cadence. The watcher's 10s debounce caps how fast new docs become visible anyway, so the default is `5s` (vs OpenSearch's `1s`) to cut segment-flush overhead. Set to `-1` to disable refresh entirely during big imports. `ensure_index()` propagates this to existing indexes via `put_settings`. |

## Ollama / embeddings

| Variable | Default | Description |
|---|---|---|
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama HTTP base. |
| `OLLAMA_EMBED_MODEL` | `nomic-embed-text` | Must produce 768-dim vectors. Changing this requires a full reindex. |
| `EMBEDDING_QUERY_CACHE_SIZE` | `256` | In-process LRU for single-text query embeddings, keyed on `(task, text)`. `0` disables. |

## Search

| Variable | Default | Description |
|---|---|---|
| `VECTOR_WEIGHT` | `0.3` | Weight on the normalized kNN score in the hybrid fusion. |
| `LEXICAL_WEIGHT` | `0.7` | Weight on the normalized BM25 score. |
| `RETRIEVER_K` | `10` | Default `k` for callers that don't override. |
| `RETRIEVER_FETCH_K` | `40` | Candidates fetched before reranking. |
| `CHUNK_SIZE` | `1000` | Max chars per chunk (used by `vault_parser`). |
| `CHUNK_OVERLAP` | `200` | Overlap between adjacent chunks. |

## Reranker

| Variable | Default | Description |
|---|---|---|
| `ENABLE_RERANKING` | `true` | Enable cross-encoder reranking. |
| `RERANKER_MODEL` | `cross-encoder/ms-marco-MiniLM-L-6-v2` | Hugging Face model. |
| `RERANKER_TOP_K` | `10` | Number of hits passed to the cross-encoder. |
| `RERANKER_PREWARM` | `true` | Load the cross-encoder at MCP startup so the first `search_notes` doesn't pay the ~4s load tax. Set `false` for fast dev iteration. Failures are logged and swallowed — on-demand load remains the fallback. |
| `RERANKER_CACHE_SIZE` | `1024` | In-process LRU keyed on `(sha256(query), chunk_hash)`. Only missing pairs are passed to the model. `0` disables. |

## Recency decay

Applied to the BM25 sub-query of `hybrid_search` (and to the BM25 side of the RRF fallback). See [Search Tuning](search-tuning.md) for how the curve works.

| Variable | Default | Description |
|---|---|---|
| `RECENCY_DECAY_ENABLED` | `true` | Master switch. `false` is a no-op (query is returned unwrapped). |
| `RECENCY_DECAY_SCALE` | `90d` | OpenSearch date-math. At this age, the decay returns `decay=0.5`. |
| `RECENCY_DECAY_WEIGHT` | `0.3` | Multiplier on the decay output. `0` short-circuits the wrapper. |

## Caches

All four are per-process, cleared on restart. Inspect them with `cache_stats` (MCP) or `obsidian-cli cache-stats`.

| Variable | Default | Cache |
|---|---|---|
| `EMBEDDING_QUERY_CACHE_SIZE` | `256` | Single-text query embeddings. |
| `RERANKER_CACHE_SIZE` | `1024` | Cross-encoder scores per `(query, chunk_hash)`. |
| `TAXONOMY_CACHE_TTL_SECONDS` | `60` | `collect_taxonomy()` TTL. Prevents 3-4× vault rescans during a bulk-tag workflow. |
| `READ_NOTE_CACHE_SIZE` | `64` | `read_note()` keyed on `(resolved_path, mtime_ns)` — edits auto-invalidate. |

Each can be set to `0` to disable the cache entirely.

## Example `.env`

```env
# Vault
OBSIDIAN_VAULT_PATH=/Users/me/vault

# OpenSearch
OPENSEARCH_HOST=localhost
OPENSEARCH_PORT=9201
OPENSEARCH_REFRESH_INTERVAL=5s

# Ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_EMBED_MODEL=nomic-embed-text
EMBEDDING_QUERY_CACHE_SIZE=256

# Search tuning
VECTOR_WEIGHT=0.3
LEXICAL_WEIGHT=0.7
RETRIEVER_K=10
RETRIEVER_FETCH_K=40

# Reranking
ENABLE_RERANKING=true
RERANKER_PREWARM=true
RERANKER_CACHE_SIZE=1024

# Bulk-tag taxonomy + read cache
TAXONOMY_CACHE_TTL_SECONDS=60
READ_NOTE_CACHE_SIZE=64

# Recency decay
RECENCY_DECAY_ENABLED=true
RECENCY_DECAY_SCALE=90d
RECENCY_DECAY_WEIGHT=0.3
```
