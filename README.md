# hybrid-obsidian-mcp

A single-user MCP (Model Context Protocol) server that turns an [Obsidian](https://obsidian.md/) vault into a first-class retrieval surface for LLMs and IDEs. Hybrid search (BM25 + kNN + cross-encoder reranking) over a local [OpenSearch](https://opensearch.org/) index, plus a write API for notes, todos, and daily logs — usable from any MCP client or from the shell.

📚 **Docs:** <https://agileresearchservices.github.io/hybrid-obsidian-mcp/> — Quick Start · [MCP Tools](https://agileresearchservices.github.io/hybrid-obsidian-mcp/mcp-tools/) · [CLI](https://agileresearchservices.github.io/hybrid-obsidian-mcp/cli/) · [Search Tuning](https://agileresearchservices.github.io/hybrid-obsidian-mcp/search-tuning/) · [Bulk Tagging](https://agileresearchservices.github.io/hybrid-obsidian-mcp/bulk-tagging/) · [Troubleshooting](https://agileresearchservices.github.io/hybrid-obsidian-mcp/troubleshooting/)

## Why

Obsidian's built-in search is great for "I remember a phrase." It doesn't help when you remember the *idea* but not the words, and it doesn't expose results to an LLM in a structured way. This project:

- **Adds semantic recall** — `nomic-embed-text` embeddings let you find notes by meaning, not just exact strings.
- **Keeps lexical precision** — BM25 still wins on file names, identifiers, and project tags. The hybrid pipeline blends both.
- **Exposes everything as MCP tools** — Claude Code, IDE plugins, and agent harnesses get 29 typed tools for search, read, write, todos, daily logs, and bulk tagging.
- **Runs entirely on your machine** — OpenSearch in Docker, Ollama for embeddings, no cloud calls.

It's not a general-purpose Obsidian plugin — it's a local retrieval backbone for the way *one* person works. The code base is ~3.2k LOC across `src/` and stays that way by design.

## Features

- **Hybrid search** — kNN + BM25 fused via an OpenSearch search pipeline (min-max normalization → weighted arithmetic mean), reranked by `ms-marco-MiniLM-L-6-v2` for final order.
- **Recency decay** — Gaussian `gauss(file_mtime)` boost on the BM25 side, tunable scale/weight. See [Search Tuning](https://agileresearchservices.github.io/hybrid-obsidian-mcp/search-tuning/).
- **Incremental indexing** — every chunk carries a `sha256` content hash; editing one paragraph only re-embeds that paragraph. Full reindex dropped from ~1 hour to a few minutes via batched Ollama calls.
- **File watcher** — `watchdog`-based daemon with 10s debounce; auto-routes create/modify/move/delete to incremental index updates.
- **Bulk LLM tagging** — multi-agent Haiku workflow that proposes frontmatter tags per note, verifies coverage, consolidates near-duplicates, and applies in batches with dry-run safety. See [Bulk Tagging](https://agileresearchservices.github.io/hybrid-obsidian-mcp/bulk-tagging/).
- **One Python codepath, two surfaces** — the same module functions back both the MCP server (`obsidian-mcp`) and the shell CLI (`obsidian-cli`). No drift between them.
- **In-process caches with introspection** — embedding LRU, reranker score LRU, taxonomy TTL, and `read_note` LRU keyed on `mtime_ns` (so edits auto-invalidate). `cache_stats` returns a single JSON snapshot.

## What it looks like

```text
$ uv run obsidian-cli search "rate limiting on the Nasuni API" --k 3
### 1. Nasuni — API rate limits (2026-04-08) [score: 0.871]
File: KMW/Customers/Nasuni/Meeting Notes.md  Type: customer-note  Tags: nasuni, api

Nasuni enforces a 100 req/min per-tenant ceiling; we hit it during the bulk
volume scan. Mitigation options discussed: exponential backoff on 429, or
per-tenant scan parallelism cap...

### 2. 2026-04-08 Daily Log [score: 0.612]
File: Daily Log/2026-04-08.md  Tags: daily-log

...Met with Nasuni team re: API throttling. Action: draft RFC for retry
budget. Owner: me, due Friday...

### 3. KMW backoff strategy [score: 0.488]
...
```

## Quick start

### Prerequisites

- Python 3.11+, [`uv`](https://docs.astral.sh/uv/) (`brew install uv`)
- Docker + Compose (runs a local OpenSearch 3.5)
- [Ollama](https://ollama.ai/) with `nomic-embed-text` (`ollama pull nomic-embed-text`)

### Install and run

```bash
# 1. Clone & install
git clone https://github.com/agileresearchservices/hybrid-obsidian-mcp.git
cd hybrid-obsidian-mcp
uv sync

# 2. Point at your vault (default works on macOS iCloud Obsidian)
echo 'OBSIDIAN_VAULT_PATH=/absolute/path/to/your/vault' > .env

# 3. Start OpenSearch
docker compose up -d

# 4. Build the initial index (slow first time, fast after)
uv run python -m src.indexer

# 5. Start the MCP server (your MCP client usually spawns this for you)
uv run obsidian-mcp

# 6. In another shell — start the watcher so the index stays fresh
uv run obsidian-watcher
```

For a hands-off setup on macOS, install the launchd plist so the watcher starts at login — see [Deployment](https://agileresearchservices.github.io/hybrid-obsidian-mcp/deployment/).

### Use it

**From an MCP client** (Claude Code, IDE plugins, agent harnesses) — connect to the server and call any of the 29 tools. Full reference: [MCP Tools](https://agileresearchservices.github.io/hybrid-obsidian-mcp/mcp-tools/).

**From the shell** — every MCP tool has a CLI mirror:

```bash
uv run obsidian-cli search "your query" --k 10 --exclude-tags archived,draft
uv run obsidian-cli list-notes --folder "Daily Log" --tags nasuni
uv run obsidian-cli add-todo "fix #123" --tags nasuni
uv run obsidian-cli daily-log append "Closed RFC review" --section "Completed Today 🎉"
uv run obsidian-cli cache-stats         # hit/miss snapshot for all four caches
uv run obsidian-cli workflow            # print the bulk-tag orchestration prompt
```

Full reference: [CLI](https://agileresearchservices.github.io/hybrid-obsidian-mcp/cli/).

## How it works

```text
Query → Ollama (nomic-embed-text, 768d)
      → OpenSearch hybrid query (kNN + BM25, recency decay on BM25)
      → obsidian_hybrid_pipeline (min-max norm + weighted arithmetic mean)
      → Cross-encoder rerank (ms-marco-MiniLM-L-6-v2)
      → Ranked SearchResult[]
```

```text
.md edit → watchdog (10s debounce) → vault_parser (frontmatter, sections, tags)
         → load {chunk_hash: embedding} for affected paths
         → re-embed only chunks whose hash changed (Ollama batched ~50 at a time)
         → OpenSearch bulk index + delete_by_query for stale chunks
```

Deeper dive: [Architecture](https://agileresearchservices.github.io/hybrid-obsidian-mcp/architecture/).

## Configuration

All knobs live in `.env`. Restart the server / watcher after edits; no reindex needed for query-time knobs (search weights, recency decay, rerank toggle, cache sizes). The most-touched ones:

```env
# Hybrid weights (don't have to sum to 1, but it's intuitive)
VECTOR_WEIGHT=0.3
LEXICAL_WEIGHT=0.7

# Recency decay on BM25 side
RECENCY_DECAY_ENABLED=true
RECENCY_DECAY_SCALE=90d       # at this age, decay function returns 0.5
RECENCY_DECAY_WEIGHT=0.3      # 0 disables; >1 lets decay dominate

# Reranker
ENABLE_RERANKING=true
RERANKER_PREWARM=true         # load cross-encoder at MCP startup
RERANKER_CACHE_SIZE=1024      # LRU keyed on (sha256(query), chunk_hash)
```

Full table: [Configuration](https://agileresearchservices.github.io/hybrid-obsidian-mcp/configuration/).

## Project layout

```text
src/
├── server.py              FastMCP tool definitions (29 tools)
├── cli.py                 obsidian-cli entrypoint — same Python as MCP
├── searcher.py            Hybrid search + RRF fallback
├── indexer.py             Full reindex + incremental + stale-chunk cleanup
├── writer.py              Notes / todos / daily logs (vault-relative paths only)
├── tagger.py              Bulk-tag workflow primitives + orchestration prompt
├── vault_parser.py        YAML frontmatter, section-aware chunking
├── embeddings.py          Ollama client, retry, batch, LRU
├── reranker.py            Cross-encoder singleton + per-pair score cache
├── opensearch_client.py   Process-wide client, index mapping, search pipeline
├── watcher.py             watchdog observer, 10s debounce
├── cache_stats.py         Single aggregator over the four in-process caches
└── config.py              All env vars with defaults
tests/                     pytest (see tests/README.md)
docs/                      MkDocs Material — published on GitHub Pages
```

## Development

```bash
uv run pytest tests/                                  # full test suite
uv run python -m src.indexer                          # full reindex
uv run python -m src.indexer --files "Daily Log/2026-05-12.md"  # incremental
uv run python -m src.searcher "your query"            # search bypassing CLI formatting
```

See [Development](https://agileresearchservices.github.io/hybrid-obsidian-mcp/development/) for tests, adding a new MCP tool, cache instrumentation, and the bulk-tag internals.

## License

Private project. Issues and PRs welcome at <https://github.com/agileresearchservices/hybrid-obsidian-mcp>.
