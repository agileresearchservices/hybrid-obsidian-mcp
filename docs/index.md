# hybrid-obsidian-mcp

An [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) server that adds **hybrid search** and **vault management** to an [Obsidian](https://obsidian.md/) vault. Search is powered by [OpenSearch](https://opensearch.org/) (BM25 + kNN) and [Ollama](https://ollama.ai/) embeddings, with optional cross-encoder reranking for quality.

## What you get

- **Hybrid search** — kNN vector similarity merged with BM25 full-text, normalized and combined inside an OpenSearch search pipeline, then reranked by a cross-encoder. Optional recency decay nudges fresher notes up the list.
- **Vault management** — create, append, and read notes; manage todos in `TODO.md`; append to dated daily logs.
- **Bulk tagging** — LLM-driven taxonomy collection, per-note tag proposals, and consolidation of near-duplicates — all with dry-run safety and verification.
- **File watcher** — `watchdog`-based watcher debounces vault edits and incrementally re-embeds only the chunks that changed.
- **One Python codepath, two surfaces** — the same functions back both the MCP server (`obsidian-mcp`) and the shell CLI (`obsidian-cli`). Anything an MCP client can do, cron and scripts can do too.

## Pick your starting point

<div class="grid cards" markdown>

- :material-rocket-launch: **[Quick Start](quickstart.md)**

    Install deps, start OpenSearch and the MCP server, run your first search.

- :material-sitemap: **[Architecture](architecture.md)**

    How indexing, search, reranking, and the file watcher fit together.

- :material-tools: **[MCP Tools Reference](mcp-tools.md)**

    Every tool exposed to MCP clients (Claude Code, IDEs, agents), with params and examples.

- :material-console: **[CLI Reference](cli.md)**

    `obsidian-cli` subcommands for scripts, cron, and ad-hoc shell use.

- :material-cog: **[Configuration](configuration.md)**

    Every `.env` knob — vault path, search weights, cache sizes, recency decay.

- :material-tune: **[Search Tuning](search-tuning.md)**

    Tag exclusion, recency decay, hybrid pipeline behaviour, RRF fallback.

- :material-cloud-upload: **[Deployment](deployment.md)**

    Local dev, macOS launchd daemon, logs, restart procedure.

- :material-flask: **[Development](development.md)**

    Tests, debugging, full and incremental reindex.

</div>

## Status

Single-tenant tool for a personal Obsidian vault. Production-stable for the maintainer's use case; not packaged for distribution. The code base is small enough (~3.2k LOC across `src/`) to read end-to-end in an afternoon.
