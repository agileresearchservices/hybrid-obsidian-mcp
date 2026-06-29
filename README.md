# hybrid-obsidian-mcp

FastMCP server for [Obsidian](https://obsidian.md/) vault write/management, plus a file watcher that mirrors the vault to a Synology NAS for search via the `synology-search` MCP.

> **v0.2.x — write/management only.** The v0.1.x hybrid search stack (OpenSearch + Ollama embeddings + cross-encoder reranking) was removed. Retrieval is now delegated to Synology FileIndexing via the `synology-search` MCP. This repo contains no search code.

## What it does

**MCP server** (`obsidian-mcp`) — 29 write/management tools: notes, todos, daily logs, vault stats, and bulk tagging. Designed for MCP clients (Claude Code, IDE plugins, agent harnesses). No search tools — those live in `synology-search`.

**Vault watcher** (`obsidian-watcher`) — `watchdog`-based daemon with a 10-second debounce. Mirrors every `.md` create/modify/move/delete to a mounted Synology SMB share via `shutil.copyfile`. Synology FileIndexing picks up the mirrored files; the `synology-search` MCP exposes lexical search over them.

**Shell CLI** (`obsidian-cli`) — same Python codepath as the MCP tools, for shell/cron/automation consumers.

## Quick start

### Prerequisites

- Python 3.11+, [`uv`](https://docs.astral.sh/uv/) (`brew install uv`)
- Synology NAS mounted at a local path (for NAS sync; watcher is a no-op when `NAS_SYNC_ENABLED=false`)

### Install

```bash
git clone https://github.com/agileresearchservices/hybrid-obsidian-mcp.git
cd hybrid-obsidian-mcp
uv sync
```

### Configure

```bash
cp .env.example .env   # or create .env manually
```

Key variables (full table in [Configuration](#configuration)):

```env
OBSIDIAN_VAULT_PATH=/absolute/path/to/your/vault
NAS_SYNC_ENABLED=true
NAS_VAULT_SYNC_PATH=/Volumes/YourNAS/obsidian-vault
```

### Run

```bash
# MCP server (your MCP client normally spawns this automatically)
uv run obsidian-mcp

# Vault watcher — mirrors .md changes to the NAS
uv run obsidian-watcher
```

On macOS, install the launchd plist to run the watcher at login:

```bash
cp com.obsidian.search-watcher.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.obsidian.search-watcher.plist
```

### MCP client config

In `.claude/settings.json` or `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "obsidian-search": {
      "command": "/path/to/uv",
      "args": ["--directory", "/path/to/hybrid-obsidian-mcp", "run", "obsidian-mcp"]
    }
  }
}
```

### Shell CLI

```bash
uv run obsidian-cli list-todos
uv run obsidian-cli add-todo "fix #123" --tags nasuni
uv run obsidian-cli daily-log append "Closed RFC review" --section "Completed Today 🎉"
uv run obsidian-cli note create "Meeting Notes" --folder "KMW/Customers" --tags nasuni,meeting
uv run obsidian-cli stats
uv run obsidian-cli workflow       # print the bulk-tag orchestration prompt
uv run obsidian-cli read-note "KMW/TODO.md"
```

## MCP tools

| Category | Tools |
|---|---|
| **Notes** | `read_note`, `note_create`, `note_append`, `recent_notes`, `vault_stats` |
| **Todos** | `list_todos`, `add_todo`, `complete_todo`, `search_todos` |
| **Daily logs** | `daily_log_view`, `daily_log_create`, `daily_log_append`, `daily_log_summary` |
| **Bulk tagging** | `bulk_tag_taxonomy`, `bulk_tag_taxonomy_topk`, `bulk_tag_list`, `bulk_tag_create_batches`, `bulk_tag_prepare`, `bulk_tag_apply`, `bulk_tag_verify`, `bulk_tag_aggregate`, `bulk_tag_consolidate`, `bulk_tag_workflow` |

## Architecture

```
MCP client / obsidian-cli
        │
        ▼
src/server.py  (FastMCP stdio — write/management tools only)
        │
        ├── src/writer.py    notes, todos, daily logs, stats
        └── src/tagger.py    bulk tag workflow

Obsidian vault .md changes
        │
        ▼
src/watcher.py  (watchdog, 10-second debounce)
        │
        ▼
src/nas_sync.py  (shutil.copyfile → mounted SMB share)
        │
        ▼
Synology FileIndexing  →  synology-search MCP  (lexical search — separate repo)
```

All write operations go through `src/writer.py`, which enforces vault-relative paths (absolute or `~`-prefixed paths are rejected). The watcher is a pure sync side-channel — it never reads from or interferes with the MCP server.

## Configuration

All settings live in `.env`. Restart the server/watcher after changes.

| Variable | Default | Purpose |
|---|---|---|
| `OBSIDIAN_VAULT_PATH` | macOS iCloud Obsidian path | Vault root — required |
| `NAS_SYNC_ENABLED` | `false` | Enable watcher → NAS mirroring |
| `NAS_VAULT_SYNC_PATH` | _(none)_ | Mounted SMB share path |
| `TAXONOMY_CACHE_TTL_SECONDS` | `60` | TTL for `collect_taxonomy()` result cache |
| `READ_NOTE_CACHE_SIZE` | `64` | LRU size for `read_note()` keyed on `(path, mtime_ns)`; `0` disables |
| `CHUNK_SIZE` | `1000` | Max chars per content excerpt (used by bulk-tag prepare) |
| `CHUNK_OVERLAP` | `200` | Overlap chars between consecutive excerpts |

## Project layout

```
src/
├── server.py          FastMCP tool definitions (29 tools)
├── cli.py             obsidian-cli entrypoint — same Python as MCP
├── writer.py          Notes / todos / daily logs (vault-relative paths enforced)
├── tagger.py          Bulk-tag workflow: taxonomy, batches, apply, verify, aggregate
├── vault_parser.py    YAML frontmatter parsing, doc-type inference, text chunking
├── watcher.py         watchdog observer, 10s debounce → NAS sync trigger
├── nas_sync.py        Mirror changed/deleted .md files to mounted Synology share
└── config.py          All env vars with defaults
tests/
com.obsidian.search-watcher.plist   launchd service definition (macOS)
```

## Development

```bash
uv run pytest tests/
```

## License

Private project. Issues and PRs welcome at <https://github.com/agileresearchservices/hybrid-obsidian-mcp>.
