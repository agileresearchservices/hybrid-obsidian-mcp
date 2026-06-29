# hybrid-obsidian-mcp

FastMCP server for [Obsidian](https://obsidian.md/) vault search, write/management, and bulk tagging.

## What it does

**MCP server** (`obsidian-mcp`) — tools for searching the vault, managing notes/todos/daily logs, and running bulk tag workflows. All operations read directly from vault `.md` files — no external services required.

**Shell CLI** (`obsidian-cli`) — same Python codepath as the MCP tools, for shell/cron/automation consumers.

## Quick start

### Prerequisites

- Python 3.11+, [`uv`](https://docs.astral.sh/uv/) (`brew install uv`)

### Install

```bash
git clone https://github.com/agileresearchservices/hybrid-obsidian-mcp.git
cd hybrid-obsidian-mcp
uv sync
```

### Configure

Create a `.env` file (or copy `.env.example` if it exists):

```env
OBSIDIAN_VAULT_PATH=/absolute/path/to/your/vault
```

### Run

```bash
# MCP server (your MCP client normally spawns this automatically)
uv run obsidian-mcp
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
uv run obsidian-cli search "nasuni standup" --limit 5
uv run obsidian-cli search "kafka" --tags nasuni --date-from 2026-06-01
uv run obsidian-cli list-notes --folder "Daily Log" --date-from 2026-06-01
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
| **Search** | `search_notes`, `list_notes` |
| **Notes** | `read_note`, `note_create`, `note_append`, `recent_notes`, `vault_stats` |
| **Todos** | `list_todos`, `add_todo`, `complete_todo`, `search_todos` |
| **Daily logs** | `daily_log_view`, `daily_log_create`, `daily_log_append`, `daily_log_summary` |
| **Bulk tagging** | `bulk_tag_taxonomy`, `bulk_tag_taxonomy_topk`, `bulk_tag_list`, `bulk_tag_create_batches`, `bulk_tag_prepare`, `bulk_tag_apply`, `bulk_tag_verify`, `bulk_tag_aggregate`, `bulk_tag_consolidate`, `bulk_tag_workflow` |

### Search tools

**`search_notes(query, tags, exclude_tags, folder, date_from, date_to, limit=10)`** — text search over vault notes. `query` matches against note title and body. All other params filter by frontmatter metadata. Results sorted by recency with content snippets.

**`list_notes(folder, tags, exclude_tags, date_from, date_to, limit=50)`** — metadata-only listing with the same filters but no text match. Useful for browsing by folder or tag.

## Architecture

```
MCP client / obsidian-cli
        │
        ▼
src/server.py  (FastMCP stdio)
        │
        ├── src/writer.py    search, notes, todos, daily logs, stats
        └── src/tagger.py    bulk tag workflow

All tools operate directly on the Obsidian vault .md files.
```

Write operations go through `src/writer.py`, which enforces vault-relative paths (absolute or `~`-prefixed paths are rejected). Search is a direct scan of the vault directory using frontmatter parsing and text matching.

## Configuration

All settings live in `.env`. Restart the server after changes.

| Variable | Default | Purpose |
|---|---|---|
| `OBSIDIAN_VAULT_PATH` | macOS iCloud Obsidian path | Vault root — required |
| `TAXONOMY_CACHE_TTL_SECONDS` | `60` | TTL for `collect_taxonomy()` result cache |
| `READ_NOTE_CACHE_SIZE` | `64` | LRU size for `read_note()` keyed on `(path, mtime_ns)`; `0` disables |
| `CHUNK_SIZE` | `1000` | Max chars per content excerpt (used by bulk-tag prepare) |
| `CHUNK_OVERLAP` | `200` | Overlap chars between consecutive excerpts |

## Project layout

```
src/
├── server.py          FastMCP tool definitions
├── cli.py             obsidian-cli entrypoint — same Python as MCP
├── writer.py          Search, notes, todos, daily logs (vault-relative paths enforced)
├── tagger.py          Bulk-tag workflow: taxonomy, batches, apply, verify, aggregate
├── vault_parser.py    YAML frontmatter parsing, doc-type inference, text chunking
└── config.py          All env vars with defaults
tests/
```

## Development

```bash
uv run pytest tests/
```

## License

Private project. Issues and PRs welcome at <https://github.com/agileresearchservices/hybrid-obsidian-mcp>.
