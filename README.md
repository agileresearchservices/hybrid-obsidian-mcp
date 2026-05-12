# hybrid-obsidian-mcp

An MCP (Model Context Protocol) server providing **hybrid search** (semantic + lexical) and **vault management** over an Obsidian vault. Powered by [OpenSearch](https://opensearch.org/) and [Ollama](https://ollama.ai/) with cross-encoder reranking for search quality.

## Features

- **Hybrid Search** — Combines BM25 full-text matching with semantic vector similarity (kNN), reranked via cross-encoder for relevance, with optional recency decay (see [Search Tuning](#search-tuning))
- **Graph Queries** — Traverse 1–2 wikilink hops from any note via `graph_neighbors` (see [Graph Queries](#graph-queries))
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

## Graph Queries

`graph_neighbors` answers: *given a note, what links to it, and what's two hops away?* It's the building block for "context around X" workflows — daily-digest pulls, project trees, customer-account neighborhoods.

### Quick usage

```bash
# Direct linkers
uv run obsidian-cli graph-neighbors "Hyrule Project"

# Two-hop neighborhood
uv run obsidian-cli graph-neighbors "KMW" --hops 2 --limit 30
```

Via MCP:

```python
mcp__obsidian-search__graph_neighbors(target="Hyrule Project", hops=2, limit=20)
```

### How it works

**1. Wikilink extraction (indexing time).** During `parse_note`, every `[[...]]` in a note's body is extracted, normalized, and stored as a `wikilinks: keyword` array on every chunk doc for that note.

Normalization (`normalize_wikilink`):
- Strip section anchor: `[[KMW#Setup]]` → `kmw`
- Strip alias: `[[KMW|the company]]` → `kmw`
- Lowercase, collapse whitespace
- Deduplicate, sort

Why per-chunk and not per-note? It matches the existing tag/title pattern — fast term/terms filters reuse the same single-index design, and `collapse: document_id` deduplicates at query time. Storage cost is small (a few short strings × chunks/note).

**2. Filtering at index time** — two real-world traps the regex handles:
- *Unclosed Slack-style code fences* (`` ``` if [[ -z "$LB_URL" ]]; then ... ``) leak bash conditionals as wikilinks. The extractor first strips fenced and inline code, then requires the wiki-link inner text to start with a non-space character and stay on one line.
- *Section-only links* (`[[#Feedback]]`) normalize to empty string after the section strip and get filtered post-normalize.

A full reindex on a 250-note vault yielded 7 genuine link targets and 0 spurious ones after these guards landed.

**3. Hop-1 query.** Single `term` query on `wikilinks`:

```json
{ "query": { "term": { "wikilinks": "hyrule project" } },
  "collapse": { "field": "document_id" },
  "sort": [{ "file_mtime": "desc" }] }
```

Returns the *notes that contain that wikilink in their body*. The collapse dedupes chunks; the sort gives recent-first ordering.

**4. Hop-2 expansion.** Union the wikilinks arrays of the hop-1 result set, subtract the original target and the hop-1 titles themselves, and issue one `terms` query against that union:

```python
hop1_outgoing = {link for note in hop1_notes for link in note["wikilinks"]}
hop1_outgoing -= {norm_target} | {normalize_wikilink(n["title"]) for n in hop1_notes}
# → terms query, then dedup by file_path against the hop-1 set
```

Each returned note carries a `hop_distance: 1 | 2` field so callers can render or weight by depth.

### Semantic — link text, not file path

`graph_neighbors("KMW")` returns notes whose body contains `[[KMW]]`, regardless of which `.md` file Obsidian would actually resolve that link to. That's the **canonical Obsidian semantic** — wikilinks are textual references first, file paths second. For v1 we don't attempt path resolution (which would require walking Obsidian's resolver: title-match → unique filename → folder-fallback). The current behavior is correct and predictable; path resolution is opt-in future work.

### Limitations (intentionally out of scope)

- **No graph viz** — this is a query primitive, not a renderer.
- **No backlink counts or link-strength scoring** — every link weighs the same.
- **No path resolution** — `[[KMW]]` and a note titled "KMW" are treated as distinct identifiers; users who want them unified can normalize their titles to match their link text.
- **No reverse direction at hop 1** — we don't return "notes the target links *to*"; only "notes that link to the target" (and at hop 2, "notes those linkers also link to").

### Reindex requirement

The `wikilinks` field is populated as notes are parsed. Existing indexed docs from before this feature shipped won't have it until they're re-indexed:
- The launchd watcher fills it incrementally as notes are touched.
- For immediate full coverage, run `python -m src.indexer` (≈45s for a 250-note vault thanks to batched embeddings).

`ensure_index` does a best-effort `put_mapping(wikilinks)` on startup so existing indices accept the new field without manual migration.

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

# Recency decay (applied to BM25 sub-query; see Search Tuning)
RECENCY_DECAY_ENABLED=true
RECENCY_DECAY_SCALE=90d     # OpenSearch date-math: 30d, 90d, 365d, ...
RECENCY_DECAY_WEIGHT=0.3    # 0 disables the boost entirely
```

## Search Tuning

### Exclude tags

Every search entry point (`search_notes` MCP tool, `obsidian-cli search`, `obsidian-cli list-notes`, `hybrid_search`, `keyword_search`, `list_notes`) accepts an `exclude_tags` argument — a comma-separated list at the MCP/CLI boundary, a `list[str]` in Python. It compiles to a `bool.must_not: { terms: { "tags.keyword": [...] } }` clause that's honored on **both** sides of the hybrid query (the kNN sub-query treats it as a negated filter, since kNN's nested filter clause only supports `must`) **and** in the RRF fallback path.

```bash
uv run obsidian-cli search "deployment notes" --exclude-tags "archived,draft"
```

### Recency decay — how it works

By default, hybrid search now applies a Gaussian decay on `file_mtime` to the BM25 sub-query. Newer notes get a multiplicative boost; the boost fades smoothly with age.

**The shape:** OpenSearch [`function_score`](https://opensearch.org/docs/latest/query-dsl/compound/function-score/) with a `gauss` decay function. Applied **only to the BM25 side** of the hybrid query (and to the BM25-equivalent in the RRF fallback). The kNN side is left untouched — wrapping a `knn` query in `function_score` is engine-dependent, and we'd rather change ranking via a knob we fully understand.

```json
{
  "function_score": {
    "query": { "multi_match": { ... } },
    "functions": [{
      "gauss": {
        "file_mtime": {
          "origin": "now",
          "scale": "90d",
          "decay":  0.5
        }
      },
      "weight": 0.3
    }],
    "score_mode": "multiply",
    "boost_mode": "multiply"
  }
}
```

**What that means concretely:**
- `origin: "now"` — the curve peaks at the current time.
- `scale: "90d"` (default) — at 90 days old, the decay function returns 0.5.
- `decay: 0.5` — defines what "at the scale" means: half the peak value.
- `weight: 0.3` — scales the decay output before multiplying into the BM25 score, so the boost is bounded in `[0, 0.3]` per doc. A brand-new doc gets `bm25 × 0.3`; a 90-day-old doc gets `bm25 × 0.15`; a 2-year-old doc gets a much smaller multiplier.
- `boost_mode: multiply` and `score_mode: multiply` — function score multiplies the underlying BM25 relevance rather than replacing it, so a strong-relevance old doc can still outrank a weak-relevance new one.

`RECENCY_DECAY_WEIGHT=0` (or `RECENCY_DECAY_ENABLED=false`) makes `_apply_recency_decay` a no-op — the query is returned unwrapped, no `function_score` overhead.

**Important caveat — the hybrid pipeline's min-max normalization compresses magnitudes.** OpenSearch's `obsidian_hybrid_pipeline` runs each sub-query's hits through a [`normalization-processor`](https://opensearch.org/docs/latest/search-plugins/search-pipelines/normalization-processor/) (min-max within the fetched window) before the weighted-arithmetic-mean combination. That preserves *ordering* inside the BM25 fetch but compresses *score gaps*. The practical effect:

- If recency decay flips the order of two BM25 candidates, that flip propagates through.
- If recency decay just widens the score gap between two same-ordered docs, normalization mostly squashes it back.
- The RRF fallback path doesn't run the pipeline, so it sees the full magnitude shift.

If you want the decay to dominate more aggressively, raise `RECENCY_DECAY_WEIGHT` past 1.0 or shorten `RECENCY_DECAY_SCALE` (e.g. `30d`). If you want it gone entirely, set `RECENCY_DECAY_WEIGHT=0`.

**Why `file_mtime` instead of the frontmatter `date` field?** `file_mtime` is present on every chunk (set during `_prepare_note_docs` from `Path.stat().st_mtime`); the `date` frontmatter field is optional and inconsistent across the vault. For "what did I touch recently," mtime is the truer signal.

### Tuning intuitions

| Goal | Action |
|------|--------|
| Strongly prefer fresh notes | `RECENCY_DECAY_SCALE=30d`, `RECENCY_DECAY_WEIGHT=0.8` |
| Subtle nudge (default) | `RECENCY_DECAY_SCALE=90d`, `RECENCY_DECAY_WEIGHT=0.3` |
| Archive-friendly search (long memory) | `RECENCY_DECAY_SCALE=365d`, `RECENCY_DECAY_WEIGHT=0.2` |
| Disable entirely | `RECENCY_DECAY_WEIGHT=0` |

After changing `.env`, restart the MCP server / watcher. No reindex needed — decay is query-time only.

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
| `wikilinks` | keyword (multi-value) | Normalized `[[link]]` targets — drives `graph_neighbors` |
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
