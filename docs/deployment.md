# Deployment

This is a single-user tool intended to run on your local machine alongside Obsidian. Two processes need to be alive: the **MCP server** (`obsidian-mcp`, started on demand by your MCP client) and the **vault watcher** (`obsidian-watcher`, long-running, keeps the index fresh).

## Local development

```bash
# Terminal 1 — OpenSearch
docker compose up -d

# Terminal 2 — MCP server (your client usually spawns this for you)
uv run obsidian-mcp

# Terminal 3 — watcher
uv run obsidian-watcher
```

Logs go to `logs/watcher.log` (and `logs/watcher.err` for the launchd setup below).

## macOS — launchd auto-start

Install the included plist so the watcher starts at login and is restarted by launchd if it dies:

```bash
cp com.obsidian.search-watcher.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.obsidian.search-watcher.plist
```

The plist runs `uv run obsidian-watcher` from the project directory and pipes stdout/stderr to `logs/`. Edit it if your project path differs from the default in the file.

To stop / restart / unload:

```bash
launchctl unload ~/Library/LaunchAgents/com.obsidian.search-watcher.plist
launchctl load   ~/Library/LaunchAgents/com.obsidian.search-watcher.plist
```

## Restart procedure after config changes

1. Edit `.env`.
2. Restart the watcher (`launchctl kickstart -k gui/$(id -u)/com.obsidian.search-watcher` on macOS, or stop and start the process).
3. Restart the MCP server (your client will respawn it on next invocation).

No reindex is needed for query-time knobs (search weights, recency decay, rerank toggles, cache sizes). A reindex **is** needed if you change the embedding model, vector dimension, chunking, or anything else that affects the embedding input — see [Development](development.md).

## OpenSearch lifecycle

The Docker Compose file pins OpenSearch to a tagged version and disables security for local-only use. The index is persisted in a named volume — `docker compose down` preserves the index; `docker compose down -v` wipes it.

If you wipe the volume, run a full reindex:

```bash
uv run python -m src.indexer
```

## Health checks

```bash
# OpenSearch up?
curl -s http://localhost:9201 | jq .

# Ollama up?
curl -s http://localhost:11434/api/tags | jq '.models[].name'

# Index state
uv run obsidian-cli stats
# Or via MCP: call index_stats / vault_stats

# Cache hit rates
uv run obsidian-cli cache-stats
```

## Troubleshooting

### No results / low relevance

1. Confirm OpenSearch responds: `curl http://localhost:9201`.
2. Check the index has docs: `uv run obsidian-cli stats`.
3. Try `--no-rerank` to see the raw hybrid order.
4. Reindex if you suspect drift: `uv run python -m src.indexer`.

### "Obsidian vault not found"

Set `OBSIDIAN_VAULT_PATH` in `.env`. Tilde-expansion is **not** performed by the shell when launchd starts the watcher — use the absolute path.

### Ollama not responding

```bash
curl http://localhost:11434
ollama list | grep nomic-embed-text
ollama pull nomic-embed-text  # if missing
```

### OpenSearch connection errors

```bash
docker ps | grep opensearch
lsof -i :9201
docker compose restart
```

### Watcher seems stuck

Tail `logs/watcher.log`. The 10s debounce means single edits take that long to show up in the index by design. For bulk edits, the watcher batches changes — wait a bit longer and check again. If the log shows the watcher is alive but `index_stats` isn't moving, OpenSearch may be unreachable; check the logs for tenacity retry messages.
