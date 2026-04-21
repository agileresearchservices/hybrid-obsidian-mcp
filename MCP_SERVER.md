# MCP Server Documentation

This FastMCP server provides hybrid search and vault management tools for the Obsidian vault, backed by OpenSearch and Ollama embeddings.

## Quick Start

```bash
# Start OpenSearch (required)
docker compose up -d

# Start the MCP server
uv run obsidian-mcp
```

The server will listen on stdio and expose 14 tools to MCP clients (Claude Code, IDEs, etc.).

## Tools Reference

### Search & Retrieval

#### `search_notes`
Hybrid search combining semantic (kNN) and lexical (BM25) matching, with optional cross-encoder reranking.

**Parameters:**
- `query` (string, required) — Natural language search query
- `k` (integer, default 5) — Number of results to return
- `tags` (string, optional) — Comma-separated tags to filter by (e.g., `"nasuni,lucille"`)
- `date_from` (string, optional) — Filter from date (YYYY-MM-DD)
- `date_to` (string, optional) — Filter to date (YYYY-MM-DD)
- `folder` (string, optional) — Filter by folder path (e.g., `"Daily Log"`, `"KMW/Customers"`)
- `rerank` (boolean, default true) — Apply cross-encoder reranking for relevance

**Returns:** Markdown-formatted results with metadata (score, tags, file path)

**Example:**
```
query: "API rate limiting strategy"
k: 10
tags: "nasuni"
folder: "KMW/Customers/Nasuni"
```

#### `list_notes`
List notes by metadata filters without full-text search (faster for large result sets).

**Parameters:**
- `folder` (string, optional) — Filter by folder (e.g., `"Daily Log"`, `"KMW"`)
- `tags` (string, optional) — Comma-separated tags
- `date_from` (string, optional) — Filter from date (YYYY-MM-DD)
- `date_to` (string, optional) — Filter to date (YYYY-MM-DD)
- `limit` (integer, default 20) — Max notes to return

**Returns:** Markdown list with title, date, path, and tags for each note

**Example:**
```
folder: "Daily Log"
date_from: "2026-04-01"
limit: 7
```

#### `read_note`
Read the full content of a specific note.

**Parameters:**
- `file_path` (string, required) — Vault-relative path (e.g., `"Daily Log/2026-04-08.md"`, `"KMW/Notes.md"`)

**Returns:** Full note content with YAML frontmatter

---

### Indexing

#### `index_notes`
Incrementally index specific files into OpenSearch (deletes existing chunks, generates new embeddings).

**Parameters:**
- `file_paths` (array of strings, required) — Paths relative to vault root

**Returns:** JSON statistics (indexed count, chunk count, etc.)

**Example:**
```json
["Daily Log/2026-04-08.md", "KMW/Customers/Nasuni/Meeting Notes.md"]
```

**Note:** The vault watcher (`com.obsidian.search-watcher.plist`) automatically calls this with a 10s debounce on file changes. Manual calls are useful for on-demand reindexing.

#### `reindex_vault`
Full vault reindex (delete all indexed data and re-crawl).

**Parameters:** None

**Returns:** JSON statistics (total notes indexed, total chunks, index size)

**Caution:** This operation takes several minutes for large vaults. Use only when necessary (e.g., after major CLAUDE.md configuration changes).

#### `index_stats`
Get current index statistics without reindexing.

**Parameters:** None

**Returns:** JSON with:
- Document count (total indexed notes)
- Chunk count (total text chunks)
- Index size (bytes)
- Top tags (with frequencies)
- Document types

---

### Todos (from `TODO.md`)

#### `list_todos`
List todos from the vault's TODO.md file.

**Parameters:**
- `tag` (string, optional) — Filter by tag (e.g., `"nasuni"`, `"lucille"`, `"inbox"`)
- `status` (string, default "open") — One of: `"open"`, `"completed"`, `"all"`
- `limit` (integer, default 50) — Max todos to return

**Returns:** Markdown list with todo text, tags, and completion status

**Example:**
```
tag: "nasuni"
status: "open"
limit: 20
```

#### `add_todo`
Add a new todo to TODO.md.

**Parameters:**
- `text` (string, required) — Todo text, optionally with inline hashtags (e.g., `"#nasuni Fix API auth"`)
- `tags` (string, optional) — Comma-separated tags (e.g., `"nasuni,review"`)

**Returns:** Success message with line ID

**Note:** Inline hashtags are auto-detected. The tool places the todo under matching section headings in TODO.md if they exist.

#### `complete_todo`
Mark a todo as completed by its line ID.

**Parameters:**
- `todo_id` (integer, required) — Line number ID (shown by `list_todos`)

**Returns:** Success message

#### `search_todos`
Search todos by text content.

**Parameters:**
- `query` (string, required) — Text to search within todos

**Returns:** Markdown list of matching todos

---

### Daily Logs

#### `daily_log_view`
Read a daily log note.

**Parameters:**
- `date` (string, optional) — Date in YYYY-MM-DD format (default: today)

**Returns:** Full daily log content

#### `daily_log_create`
Create a daily log from the standard template.

**Parameters:**
- `date` (string, optional) — Date in YYYY-MM-DD format (default: today)
- `force` (boolean, default false) — Overwrite if log already exists

**Returns:** Success message

#### `daily_log_append`
Append content to a daily log (creates from template if needed).

**Parameters:**
- `content` (string, required) — Markdown content to append
- `section` (string, optional) — Section heading to append under (e.g., `"Notes 📝"`, `"Tasks ✅"`, `"Completed Today 🎉"`)
- `date` (string, optional) — Date in YYYY-MM-DD format (default: today)

**Returns:** Success message

**Example:**
```
content: "Investigated OpenSearch cluster latency"
section: "Completed Today 🎉"
date: "2026-04-21"
```

#### `daily_log_summary`
Show which daily logs exist for the last N days.

**Parameters:**
- `days` (integer, default 7) — Number of days to look back

**Returns:** Markdown list of dates with notes on whether logs exist

---

### Note Management

#### `note_create`
Create a new note with YAML frontmatter.

**Parameters:**
- `title` (string, required) — Note title (also used as filename)
- `content` (string, default "") — Initial body content (markdown)
- `folder` (string, optional) — Vault-relative folder path (e.g., `"KMW/Customers/Nasuni"`)
- `tags` (string, optional) — Comma-separated tags (e.g., `"nasuni,opensearch"`)

**Returns:** Success message with file path

#### `note_append`
Append markdown content to the end of an existing note.

**Parameters:**
- `file_path` (string, required) — Vault-relative path (e.g., `"KMW/Customers/Nasuni/Meeting Notes.md"`)
- `content` (string, required) — Markdown content to append

**Returns:** Success message

#### `recent_notes`
List recently modified notes in the vault.

**Parameters:**
- `limit` (integer, default 10) — Max notes to return

**Returns:** Markdown list with title, date, and path for each note

#### `vault_stats`
Show overall vault statistics.

**Parameters:** None

**Returns:** JSON with:
- Total note count
- Vault size (bytes)
- Todo counts (open, completed)
- Top tags (with frequencies)

---

## Configuration

Configuration is loaded from `.env` with defaults (see `src/config.py`):

```env
# Vault path
OBSIDIAN_VAULT_PATH=~/Library/Mobile Documents/iCloud~md~obsidian/Documents/obsidian-vault

# OpenSearch
OPENSEARCH_HOST=localhost
OPENSEARCH_PORT=9201
OPENSEARCH_INDEX=obsidian_notes

# Ollama embeddings
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_EMBED_MODEL=nomic-embed-text
VECTOR_DIMENSION=768

# Search weighting (hybrid score = vector_weight * norm_knn_score + lexical_weight * norm_bm25_score)
VECTOR_WEIGHT=0.3
LEXICAL_WEIGHT=0.7

# Retrieval settings
RETRIEVER_K=10        # Results returned by default
RETRIEVER_FETCH_K=40  # Candidates before reranking

# Reranking (cross-encoder)
ENABLE_RERANKING=true
```

---

## How It Works

### Search Pipeline

1. **Query embedding:** User query → Ollama `nomic-embed-text` → 768-dim vector
2. **Hybrid query:** OpenSearch performs:
   - **kNN search:** Top-40 candidates by vector similarity (cosine)
   - **BM25 search:** Top-40 candidates by keyword match
3. **Score normalization & merge:** Min-max normalize both scores, weighted sum (0.3 * kNN + 0.7 * BM25)
4. **Top-K:** Return top-10 by combined score
5. **Reranking (optional):** Cross-encoder (`ms-marco-MiniLM-L-6-v2`) reranks top-10 for final relevance order

### Indexing Pipeline

1. **Vault scan:** Find all `.md` files
2. **Parse:** Extract YAML frontmatter, chunk by section (max 1000 chars per chunk)
3. **Embed:** Send chunks to Ollama, get 768-dim vectors
4. **Index:** Bulk index to OpenSearch with metadata (tags, folder, date, file path)
5. **Debounce:** File watcher auto-indexes changes with 10s debounce

---

## Troubleshooting

### "No results found" or low relevance

1. **Check index health:**
   ```bash
   python -m src.searcher "test query"
   ```

2. **Verify OpenSearch is running:**
   ```bash
   curl http://localhost:9201
   ```

3. **Try keyword search (BM25 only):**
   - Disable reranking (`rerank=false`)
   - Check if the exact phrase appears in your vault

4. **Reindex if needed:**
   ```bash
   python -m src.indexer
   ```

### "Obsidian vault not found"

- Verify `OBSIDIAN_VAULT_PATH` in `.env`
- macOS path must be expanded (e.g., `~/Library/...`, not `$HOME/...`)

### Ollama not responding

- Verify Ollama is running: `curl http://localhost:11434`
- Check model is available: `ollama list | grep nomic-embed-text`
- Pull if missing: `ollama pull nomic-embed-text`

### OpenSearch connection errors

- Verify OpenSearch is running: `docker ps | grep opensearch`
- Check port 9201 is exposed: `lsof -i :9201`
- Restart if needed: `docker compose restart opensearch`

---

## Integration with Claude Code

The MCP server is designed for use with Claude Code and other MCP clients.

To enable in Claude Code:

1. Ensure the server is running: `uv run obsidian-mcp`
2. Claude Code will automatically discover and expose the 14 tools
3. Use tools directly in prompts (e.g., `search_notes("my query")`)

Tools can also be called from the bash terminal once the server starts:

```bash
# Via MCP client
python -m src.searcher "query"

# Bulk operations
python -m src.indexer --files "Daily Log/2026-04-08.md"
python -m src.indexer  # Full reindex
```

---

## Advanced: Custom Search Queries

For power users, OpenSearch queries can be sent directly via the Python client:

```python
from src.opensearch_client import get_client

client = get_client()
results = client.search(
    index="obsidian_notes",
    body={
        "query": {
            "bool": {
                "must": [
                    {"match": {"chunk_text": "API rate limiting"}}
                ],
                "filter": [
                    {"term": {"tags": "nasuni"}}
                ]
            }
        }
    }
)
```

See the `src/searcher.py` module for the hybrid pipeline implementation.
