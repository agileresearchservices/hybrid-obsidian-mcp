# MCP Tools Reference

The MCP server (`obsidian-mcp`) is built on [FastMCP](https://github.com/jlowin/fastmcp) and exposes the tools below over stdio. Tool signatures and docstrings come from `src/server.py`; every tool calls into the same Python codepath that `obsidian-cli` does.

Connect from any MCP client (Claude Code, IDE plugins, agent harnesses) and the tools surface automatically.

## Search & retrieval

### `search_notes`

Hybrid search (kNN + BM25) with optional cross-encoder reranking.

| Param | Type | Default | Description |
|---|---|---|---|
| `query` | string | _required_ | Natural language query. |
| `k` | int | `5` | Results to return. |
| `tags` | string | `null` | Comma-separated tags to require, e.g. `"nasuni,lucille"`. |
| `date_from` | string | `null` | `YYYY-MM-DD` lower bound on the frontmatter `date` field. |
| `date_to` | string | `null` | `YYYY-MM-DD` upper bound. |
| `folder` | string | `null` | Folder filter, e.g. `"KMW/Customers"`. |
| `exclude_tags` | string | `null` | Comma-separated tags to exclude, e.g. `"archived,draft"`. |
| `rerank` | bool | `true` | Apply cross-encoder reranking. |

Returns markdown with one entry per result: title, date, score, file path, tags, and the chunk text.

### `list_notes`

List notes matching metadata filters without running full-text search. Faster than `search_notes` for "show me everything tagged X" queries.

| Param | Type | Default | Description |
|---|---|---|---|
| `folder` | string | `null` | Folder filter. |
| `tags` | string | `null` | Comma-separated tags to require. |
| `date_from` | string | `null` | `YYYY-MM-DD` lower bound. |
| `date_to` | string | `null` | `YYYY-MM-DD` upper bound. |
| `limit` | int | `20` | Max notes. |
| `exclude_tags` | string | `null` | Comma-separated tags to exclude. |

### `read_note`

Returns the full markdown of one note (frontmatter included). Results are LRU-cached on `(path, mtime_ns)` — edits invalidate automatically.

| Param | Type | Default | Description |
|---|---|---|---|
| `file_path` | string | _required_ | Vault-relative path, e.g. `"Daily Log/2026-04-08.md"`. |

## Indexing

### `index_notes`

Incrementally re-index specific files. Deletes existing chunks for each path and re-embeds — but unchanged chunks reuse cached vectors via `chunk_hash`, so the cost is proportional to *what changed*, not file size.

| Param | Type | Description |
|---|---|---|
| `file_paths` | list[string] | Vault-relative paths. |

Returns JSON `{indexed, chunks, cache_hits, cache_misses, ...}`.

The watcher calls this automatically with a 10s debounce — manual calls are mostly for on-demand reindexing.

### `reindex_vault`

Full vault reindex — delete the index and re-crawl. Takes several minutes for a large vault. Reserve for index-schema or chunking changes.

### `index_stats`

Snapshot of the index: doc count, chunk count, byte size, top tags, doc types.

### `cache_stats`

Snapshot of the four in-process caches (embedding / reranker / taxonomy / `read_note`). Returns JSON with hits, misses, sizes, and `hit_rate` per cache. If `hit_rate` is `null` or low, either the workload doesn't repeat or the cache is undersized — see [Configuration](configuration.md).

## Todos (`TODO.md`)

### `list_todos`

| Param | Type | Default | Description |
|---|---|---|---|
| `tag` | string | `null` | Filter by tag. |
| `status` | string | `"open"` | One of `open` / `completed` / `all`. |
| `limit` | int | `50` | Max todos. |

### `add_todo`

| Param | Type | Description |
|---|---|---|
| `text` | string | Todo text. Inline `#hashtags` are auto-detected. |
| `tags` | string | Optional comma-separated tags. |

The todo is placed under the matching section heading in `TODO.md` if one exists.

### `complete_todo`

| Param | Type | Description |
|---|---|---|
| `todo_id` | int | Line ID shown by `list_todos`. |

### `search_todos`

| Param | Type | Description |
|---|---|---|
| `query` | string | Substring to match in todo text. |

## Daily logs

### `daily_log_view`

| Param | Type | Default | Description |
|---|---|---|---|
| `date` | string | today | `YYYY-MM-DD`. |

### `daily_log_create`

| Param | Type | Default | Description |
|---|---|---|---|
| `date` | string | today | `YYYY-MM-DD`. |
| `force` | bool | `false` | Overwrite if exists. |

### `daily_log_append`

| Param | Type | Default | Description |
|---|---|---|---|
| `content` | string | _required_ | Markdown to append. |
| `section` | string | `null` | Heading to insert under, e.g. `"Notes 📝"`, `"Tasks ✅"`, `"Completed Today 🎉"`. |
| `date` | string | today | `YYYY-MM-DD`. |

Creates the daily log from the standard template first if it doesn't exist.

### `daily_log_summary`

| Param | Type | Default | Description |
|---|---|---|---|
| `days` | int | `7` | Lookback window. |

## Notes

### `note_create`

| Param | Type | Default | Description |
|---|---|---|---|
| `title` | string | _required_ | Note title (also the filename). |
| `content` | string | `""` | Initial markdown body. |
| `folder` | string | `null` | Vault-relative folder. |
| `tags` | string | `null` | Comma-separated tags. |

### `note_append`

| Param | Type | Description |
|---|---|---|
| `file_path` | string | Vault-relative path. |
| `content` | string | Markdown to append. |

### `recent_notes`

| Param | Type | Default | Description |
|---|---|---|---|
| `limit` | int | `10` | Max notes. |

### `vault_stats`

Returns note count, vault size, todo counts, and top tags.

## Bulk tagging

The bulk-tag workflow is built so it can be driven end-to-end by an LLM. `bulk_tag_workflow` returns the orchestration prompt; the rest are the primitives that prompt walks through.

| Tool | Purpose |
|---|---|
| `bulk_tag_workflow` | Returns the orchestration prompt. Call this first. |
| `bulk_tag_taxonomy` | Current vault taxonomy as `{tag: count}` JSON. |
| `bulk_tag_taxonomy_topk` | Top-K tags as a newline list (token-efficient for subagents). |
| `bulk_tag_list` | Every `.md` note with path/size/folder. |
| `bulk_tag_create_batches` | Split paths into `batch_NN.json` files for parallel agents. |
| `bulk_tag_prepare` | Per-note `existing_tags` + content excerpt — one call replaces N `read_note`s. |
| `bulk_tag_verify` | Validate a result file covers its batch input and isn't stale. |
| `bulk_tag_aggregate` | Flatten batch results, drop blocklisted and singleton tags. |
| `bulk_tag_consolidate` | Auto-merge near-duplicate tags above a confidence threshold. |
| `bulk_tag_apply` | Apply `[{path, add_tags, remove_tags}]` to the vault (supports `dry_run`). |

`bulk_tag_apply` validates **every** path before any write. If any is missing, the whole batch aborts with `status=preflight_failed` and zero files are touched. Tags are normalized to lowercase kebab-case, deduplicated, aliased, capped at `MAX_NEW_TAGS_PER_NOTE` per note, and merged into existing frontmatter.

All classification subagent work in the workflow uses Haiku for cost efficiency.

## Path safety

`writer.note_create`, `writer.note_append`, and other write paths reject absolute and `~`-prefixed paths. Everything must be vault-relative, e.g. `"KMW/Customers/Nasuni/Meeting Notes.md"`. See `tests/test_writer_paths.py` for the contract.
