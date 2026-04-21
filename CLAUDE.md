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

# Shell CLI (same Python as the MCP server — used by slack-gateway, daily-digest, cron, etc.)
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
2. Chunks → Ollama for embeddings → OpenSearch bulk index

**Module responsibilities:**
- `src/server.py` — FastMCP tool definitions (search, index, todos, daily logs, notes, bulk-tag)
- `src/cli.py` — `obsidian-cli` shell entrypoint; same codepath as MCP tools. Used by slack-gateway, daily-digest, and any other automation.
- `src/searcher.py` — Hybrid search (kNN + BM25), keyword search, list/filter by metadata
- `src/indexer.py` — Full reindex and incremental `index_files()` for specific paths
- `src/writer.py` — Vault write operations: todos, daily logs, note create/append
- `src/tagger.py` — Bulk tag operations: taxonomy collection, frontmatter merges, workflow prompt
- `src/vault_parser.py` — YAML frontmatter, section-aware chunking, tag/wikilink extraction
- `src/opensearch_client.py` — Client setup, index mapping (768-dim HNSW), hybrid search pipeline
- `src/reranker.py` — Lazy-loaded cross-encoder singleton; disabled via `ENABLE_RERANKING=false`
- `src/watcher.py` — watchdog-based file watcher with 10s debounce → calls `index_files()`
- `src/config.py` — All config from `.env` with defaults

**Single source of truth**: this project owns every Obsidian vault operation. The former `~/.claude/skills/obsidian/` skill has been deleted; slack-gateway and daily-digest now call `obsidian-cli` directly.

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
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_EMBED_MODEL=nomic-embed-text
VECTOR_WEIGHT=0.3       # weight in hybrid score
LEXICAL_WEIGHT=0.7
RETRIEVER_K=10          # results returned
RETRIEVER_FETCH_K=40    # candidates before reranking
ENABLE_RERANKING=true
```

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
