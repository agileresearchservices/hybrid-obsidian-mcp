# hybrid-obsidian-mcp

An MCP (Model Context Protocol) server providing **hybrid search** (semantic + lexical) and **vault management** over an Obsidian vault. Powered by [OpenSearch](https://opensearch.org/) and [Ollama](https://ollama.ai/) with cross-encoder reranking for search quality.

## Features

- **Hybrid Search** — Combines BM25 full-text matching with semantic vector similarity (kNN), reranked via cross-encoder for relevance
- **Full-Text Indexing** — Frontmatter-aware chunking with tag and wikilink extraction
- **Vault Management** — CRUD operations for notes, todos, and daily logs
- **Bulk Tagging** — LLM-powered taxonomy collection and note tagging with consolidation
- **File Watcher** — Auto-indexes vault changes with 10s debounce
- **CLI & MCP** — Same Python codepath: use as MCP tool or `obsidian-cli` for automation

## Quick Start

### 1. Prerequisites

- Docker & Docker Compose (for OpenSearch)
- Ollama running locally with `nomic-embed-text` model
- Python 3.11+
- uv package manager

### 2. Setup

```bash
# Install dependencies
uv sync

# Start OpenSearch
docker compose up -d

# Run the MCP server
uv run obsidian-mcp

# In another terminal, run the vault watcher
uv run obsidian-watcher
```

### 3. Use

#### Via MCP (Claude, IDEs, integrations)
Connect the MCP server to your tool, then call:
- `search_notes` — hybrid search with filters
- `index_notes` — re-index specific files
- `read_note`, `note_create`, `note_append` — note operations
- `add_todo`, `list_todos`, `complete_todo` — todo management
- `daily_log_view`, `daily_log_append` — daily log operations
- `bulk_tag_workflow` — orchestrate LLM-powered tagging

#### Via CLI (scripts, cron, automation)
```bash
# Search
uv run obsidian-cli search "your query"

# List notes
uv run obsidian-cli list-notes --folder "Daily Log" --tags nasuni

# Manage todos
uv run obsidian-cli list-todos --tag nasuni
uv run obsidian-cli add-todo "fix bug #123" --tags nasuni

# Daily log
uv run obsidian-cli daily-log view
uv run obsidian-cli daily-log append "Meeting with team" --section "Notes"

# Bulk tagging
uv run obsidian-cli bulk-tag-workflow
```

## Architecture

**Core Components:**

| Module | Purpose | LOC |
|--------|---------|-----|
| `src/server.py` | FastMCP tool definitions | 459 |
| `src/searcher.py` | Hybrid search (kNN + BM25) | 332 |
| `src/indexer.py` | Full/incremental vault indexing | 296 |
| `src/tagger.py` | Bulk tag operations & consolidation | 706 |
| `src/writer.py` | Vault writes (notes, todos, logs) | 426 |
| `src/cli.py` | Command-line interface | 398 |
| `src/vault_parser.py` | YAML frontmatter & chunking | 210 |
| `src/opensearch_client.py` | OpenSearch setup & pipelines | 120 |
| `src/reranker.py` | Cross-encoder reranking | 97 |
| `src/watcher.py` | File watcher with debounce | 134 |
| `src/config.py` | Configuration from `.env` | 44 |

**Data Flow:**

*Search:*
```
Query → Ollama embeddings
      ↓
OpenSearch hybrid query (vector + BM25)
      ↓
Cross-encoder reranking
      ↓
Sorted results
```

*Indexing:*
```
.md files (frontmatter + sections)
      ↓
Vault parser (chunking, tag extraction)
      ↓
Ollama embeddings (768-dim `nomic-embed-text`)
      ↓
OpenSearch bulk index
```

## Configuration

Edit `.env` to override defaults:

```env
# Vault path (auto-detected on macOS)
OBSIDIAN_VAULT_PATH=~/Library/Mobile Documents/iCloud~md~obsidian/Documents/obsidian-vault

# OpenSearch
OPENSEARCH_HOST=localhost
OPENSEARCH_PORT=9201

# Ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_EMBED_MODEL=nomic-embed-text

# Search tuning
VECTOR_WEIGHT=0.3           # How much to weight semantic similarity
LEXICAL_WEIGHT=0.7          # How much to weight BM25 relevance
RETRIEVER_K=10              # Results returned to user
RETRIEVER_FETCH_K=40        # Candidates considered before reranking

# Reranking
ENABLE_RERANKING=true       # Use cross-encoder (slower but higher quality)
```

## Deployment

### Local Development

The watcher runs indefinitely, auto-indexing vault changes. Logs go to `logs/watcher.log`.

### macOS Auto-Start

Install the launchd daemon to keep the watcher running at startup:

```bash
cp com.obsidian.search-watcher.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.obsidian.search-watcher.plist
```

## Index Schema

**Index:** `obsidian_notes`

| Field | Type | Purpose |
|-------|------|---------|
| `embedding` | knn_vector (768 dims, HNSW) | Semantic search |
| `chunk_text` | text (English analyzer) | Full-text search |
| `tags` | keyword | Faceted filtering |
| `folder` | keyword | Folder filtering |
| `doc_type` | keyword | Note type (daily-log, todo, etc.) |
| `file_path` | keyword | Source file path |
| `date` | date | Date filtering (YYYY-MM-DD) |

**Search Pipeline:** `obsidian_hybrid_pipeline` — min-max normalization + weighted arithmetic mean.

## Development

For more details on development, architecture decisions, and debugging, see [CLAUDE.md](CLAUDE.md).

Key commands:
```bash
# Full reindex (slow)
python -m src.indexer

# Incremental reindex (fast)
python -m src.indexer --files "Daily Log/2026-05-12.md"

# Search from CLI
python -m src.searcher "your query"

# View vault stats
uv run obsidian-cli stats
```

## License

Private project.
