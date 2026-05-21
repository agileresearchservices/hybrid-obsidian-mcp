# Quick Start

## Prerequisites

| Dependency | Why | Install |
|---|---|---|
| Python 3.11+ | Server runtime | [python.org](https://www.python.org/downloads/) |
| [`uv`](https://docs.astral.sh/uv/) | Package manager / project runner | `pipx install uv` or `brew install uv` |
| Docker + Compose | Runs OpenSearch locally | [docker.com](https://www.docker.com/get-started) |
| [Ollama](https://ollama.ai/) | Hosts the embedding model | `brew install ollama` or platform installer |
| `nomic-embed-text` model | Embeddings (768-dim) | `ollama pull nomic-embed-text` |

## 1. Clone and install

```bash
git clone https://github.com/agileresearchservices/hybrid-obsidian-mcp.git
cd hybrid-obsidian-mcp
uv sync
```

## 2. Point at your vault

Create a `.env` in the project root with at least:

```env
OBSIDIAN_VAULT_PATH=/absolute/path/to/your/vault
```

On macOS with iCloud-synced Obsidian, the default is already correct — you can omit the variable. See [Configuration](configuration.md) for the full list of knobs.

## 3. Start OpenSearch

```bash
docker compose up -d
```

This brings up a single-node OpenSearch 3.5.0 on `localhost:9201` with security disabled. The index `obsidian_notes` is created on first use with a 768-dim HNSW vector field, plus a search pipeline that normalizes and weights the BM25 + kNN scores.

Verify:

```bash
curl http://localhost:9201
```

## 4. Build the initial index

```bash
# Full vault crawl — embed every chunk, bulk-index. Slow the first time.
uv run python -m src.indexer
```

Re-runs are much faster after the initial index is built; the watcher (see below) keeps it fresh from then on.

## 5. Start the MCP server

```bash
uv run obsidian-mcp
```

The server listens on stdio and exposes its tools to any MCP client (Claude Code, an IDE plugin, an agent harness). With default settings the cross-encoder pre-warms at startup, so the first `search_notes` query doesn't pay the ~4s model-load tax.

## 6. Start the watcher

In another terminal:

```bash
uv run obsidian-watcher
```

The watcher tails the vault, debounces edits by 10s, and re-embeds only the chunks whose content actually changed (every doc stores a `chunk_hash`, so unchanged chunks reuse cached vectors).

For a hands-off setup on macOS, install the launchd daemon so the watcher starts at login — see [Deployment](deployment.md).

## 7. Search

From the CLI:

```bash
uv run obsidian-cli search "API rate limiting strategy" --k 10
```

From an MCP client, call `search_notes`. The [MCP Tools Reference](mcp-tools.md) lists every available tool.

## Next steps

- Read the [Architecture](architecture.md) overview to understand what's happening under the hood.
- Tune relevance via [Search Tuning](search-tuning.md) (recency decay, tag exclusions, hybrid weights).
- Wire the watcher into launchd or systemd so you can forget about it — see [Deployment](deployment.md).
