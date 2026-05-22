# Troubleshooting

Symptoms first, then the checks that pin down the cause. When something is wrong, [`cache_stats`](mcp-tools.md#cache_stats), [`index_stats`](mcp-tools.md#index_stats), and the `logs/watcher.log` tail are usually enough to localize it.

## Quick health check

```bash
# OpenSearch reachable?
curl -s http://localhost:9201 | jq .

# Ollama up + embedding model present?
curl -s http://localhost:11434/api/tags | jq '.models[].name' | grep nomic-embed-text

# Index has docs?
uv run obsidian-cli stats

# Caches doing work?
uv run obsidian-cli cache-stats

# Watcher alive?
tail -n 50 logs/watcher.log
```

If any of those four returns nothing, that's your starting point.

## "No results found" / low relevance

1. **Confirm the index has documents.**
   ```bash
   uv run obsidian-cli stats
   ```
   `note_count: 0` or `chunk_count: 0` means the initial reindex never ran. Fix: `uv run python -m src.indexer`.

2. **Try the raw hybrid order.** Reranking sometimes reorders surprisingly. Pass `--no-rerank` from the CLI or `rerank=false` from MCP to see the pre-rerank order.

3. **Try `list_notes` with the same filters.** If `list_notes folder="X" tags="Y"` returns nothing, the filters themselves are too strict — the search isn't the problem.

4. **Try keyword-only.** Bump `LEXICAL_WEIGHT=1.0`, `VECTOR_WEIGHT=0.0` temporarily in `.env`, restart, and re-search. If a phrase that should exist shows up, the kNN side is misleading the fusion — investigate whether the chunk text actually contains the query terms.

5. **Reindex if you suspect drift.** Watcher misses, embed model changes, or chunking changes need a full reindex:
   ```bash
   uv run python -m src.indexer
   ```

## "Obsidian vault not found"

`OBSIDIAN_VAULT_PATH` is wrong, unset, or not expanded. Two specific traps:

- **launchd doesn't expand `~`.** When the watcher runs via launchd, the shell isn't involved; tilde-expansion silently fails. Always use the **absolute** path in `.env` and in `com.obsidian.search-watcher.plist`.
- **iCloud path contains a tilde-looking character.** The actual path has `iCloud~md~obsidian` in it — those are literal `~` characters in the directory name, not home-dir markers. Don't escape them.

Verify:
```bash
uv run obsidian-cli config
ls -la "$(uv run obsidian-cli config | jq -r .vault_path)"
```

## Ollama not responding

```bash
curl http://localhost:11434
ollama list | grep nomic-embed-text
ollama pull nomic-embed-text  # if missing
```

If Ollama is up but `embeddings.get_embedding()` times out, check that `OLLAMA_BASE_URL` points at the right port. The default `http://localhost:11434` is the Ollama default but some setups override it.

For batched indexing, tenacity will retry transient failures up to its configured limit before propagating. If you're seeing retry storms in `logs/watcher.log`, Ollama is probably overwhelmed by parallel batches — usually a memory-pressure issue on the embedding model. Restart Ollama and retry.

## OpenSearch connection errors

```bash
docker ps | grep opensearch
lsof -i :9201
docker compose restart opensearch
docker compose logs --tail 200 opensearch
```

`opensearch_client.create_client()` is a process-wide singleton. If you killed the container and brought it back up, the first request from the **same** Python process may hit a stale connection — restart the MCP server / watcher to pick up the new container.

## Watcher seems stuck

```bash
tail -f logs/watcher.log
```

A few common patterns:

| What you see | What it means | Fix |
|---|---|---|
| No log lines after edits | watchdog observer died or vault path is wrong | Check `OBSIDIAN_VAULT_PATH`; restart watcher |
| Tenacity retries against OpenSearch | OpenSearch unreachable | `docker compose ps` |
| 10s gap between edit and reindex | Working as designed (debounce) | Wait — single edits take ≥10s by design |
| Long stall after bulk paste | Batch reindex in progress | Watch for "indexed N chunks" log line |
| `cache_misses` >> `cache_hits` on incremental | Either heavy edits or a parser change invalidated all hashes | Expected — re-run, second pass will hit cache |

Restart the launchd watcher:

```bash
launchctl kickstart -k gui/$(id -u)/com.obsidian.search-watcher
```

## "Slow first search" (~4s)

The cross-encoder is loading. Check:

```env
RERANKER_PREWARM=true
ENABLE_RERANKING=true
```

`RERANKER_PREWARM=true` (the default) loads the model at MCP startup so the first query doesn't pay the load tax. Prewarm failures are logged in the MCP server output and **swallowed** — the on-demand load path remains the fallback, which is what produces the 4s delay. If you see prewarm errors, the cross-encoder model download or Hugging Face cache is the culprit.

For dev iteration where startup speed matters more than first-query speed, flip to `RERANKER_PREWARM=false`.

## "Hit rate is `null` / low"

`null` from `cache_stats` means the cache had zero accesses since process start — not that it's broken. Run the workload, check again.

Low (but non-null) hit rate is more interesting:

| Cache | Diagnostic |
|---|---|
| Embedding query | Workload is doing many distinct one-off queries (expected for exploratory use); bump `EMBEDDING_QUERY_CACHE_SIZE` if memory allows |
| Reranker | Same — bump `RERANKER_CACHE_SIZE`. The key includes `chunk_hash`, so chunk edits force misses too |
| Taxonomy | TTL too short for the workload, or the vault is being rescanned more than 60s apart — bump `TAXONOMY_CACHE_TTL_SECONDS` |
| `read_note` | Mostly useful during bulk-tag runs; outside of that, hit rate stays low and that's fine |

## `bulk_tag_apply` returns `status=preflight_failed`

The change set contains a path the resolver couldn't match to an actual file. Common causes:

- **LLM rewrote a filename.** Curly quotes, en/em-dashes, NFC drift — `_canonicalize_path` folds the common cases but anything unusual fails preflight (deliberately).
- **File deleted between propose and apply.** Re-run the workflow; the second pass picks up the current vault state.
- **Path encoding mismatch.** Look at `preflight_errors` in the response — it lists each path with the specific failure (`not_found`, `outside_vault`, `not_a_file`).

Preflight is **atomic** — no writes happen if any path fails. Fix the offending entries, re-call `bulk_tag_apply`.

## Tests fail

```bash
uv run pytest tests/ -v
```

Tests are designed to run without OpenSearch or Ollama running — they mock those layers. If you're seeing connection errors in tests, a recent change probably skipped a mock. See [Development](development.md#run-the-tests) and [tests/README.md](https://github.com/agileresearchservices/hybrid-obsidian-mcp/blob/main/tests/README.md).

## Resetting from scratch

When nothing else works:

```bash
# Stop everything
launchctl unload ~/Library/LaunchAgents/com.obsidian.search-watcher.plist 2>/dev/null
docker compose down

# Wipe the OpenSearch volume (the vault is untouched — only the index dies)
docker compose down -v

# Bring everything back
docker compose up -d
uv run python -m src.indexer      # full reindex
launchctl load ~/Library/LaunchAgents/com.obsidian.search-watcher.plist
```

This is the "factory reset" — it loses the index (rebuildable) and the in-process caches (rebuildable). It does **not** touch your vault.
