# Development

## Layout

```text
hybrid-obsidian-mcp/
├── src/                # All application code
│   ├── server.py       # FastMCP tool definitions
│   ├── cli.py          # obsidian-cli entrypoint
│   ├── searcher.py     # Hybrid + RRF search
│   ├── indexer.py      # Full + incremental indexing
│   ├── writer.py       # Vault writes (notes, todos, daily logs)
│   ├── tagger.py       # Bulk tag workflow
│   ├── vault_parser.py # Frontmatter + section-aware chunking
│   ├── embeddings.py   # Ollama client + LRU
│   ├── reranker.py     # Cross-encoder + score cache
│   ├── opensearch_client.py
│   ├── watcher.py
│   ├── cache_stats.py
│   └── config.py
├── tests/              # pytest
├── docs/               # MkDocs site (this site)
├── mkdocs.yml
├── docker-compose.yml
├── pyproject.toml
└── com.obsidian.search-watcher.plist
```

## Run the tests

```bash
uv run pytest tests/
```

Notable test files:

| File | Covers |
|---|---|
| `test_embeddings.py` | Ollama client, retries, batching. |
| `test_embed_cache.py` | `chunk_hash`-based vector reuse on incremental index. |
| `test_writer_paths.py` | Path safety — rejects absolute and `~`-prefixed paths. |
| `test_tagger.py` | Bulk-tag normalization, aliases, blocklist, dry-run. |
| `test_searcher_filters.py` | Tag / folder / date filters, `exclude_tags`. |
| `test_reranker.py` | Cross-encoder score cache behaviour. |
| `test_opensearch_client.py` | Client singleton, `ensure_index` settings sync. |
| `test_server.py` | MCP tool wiring. |
| `test_vault_parser.py` | Frontmatter, chunking, tag extraction. |
| `test_cache_stats.py` | The unified cache snapshot. |

## Reindexing

```bash
# Full reindex — wipes the index and re-embeds everything. Slow.
uv run python -m src.indexer

# Incremental — just these files. Reuses cached vectors for unchanged chunks.
uv run python -m src.indexer --files "Daily Log/2026-04-08.md" "KMW/Notes.md"
```

The full reindex is necessary when:

- You change `OLLAMA_EMBED_MODEL` (different vector space).
- You change `CHUNK_SIZE` / `CHUNK_OVERLAP` (different chunk boundaries).
- You change anything that affects the `chunk_hash` input (parser changes, tag normalization rules).

Query-time changes (search weights, recency decay, rerank toggle, cache sizes) **do not** need a reindex — just restart the MCP server / watcher.

## Searching from a shell

```bash
uv run python -m src.searcher "your query"
# or
uv run obsidian-cli search "your query"
```

`python -m src.searcher` is the lower-level entrypoint — useful when you want to bypass the CLI's output formatting.

## Adding a new MCP tool

1. Add the function to the right module (`writer`, `searcher`, `indexer`, `tagger`).
2. Wrap it in `src/server.py` with `@mcp.tool()` — the docstring is the LLM-facing description.
3. Mirror it as an `obsidian-cli` subcommand in `src/cli.py` if shell access is useful.
4. Cover the new path in `tests/`.

The same Python function should back both surfaces — don't fork logic between the MCP and CLI layers.

## Bulk-tag workflow internals

`bulk_tag_workflow()` returns an orchestration prompt that walks an LLM through:

1. `bulk_tag_taxonomy_topk(k=100)` — seed taxonomy.
2. `bulk_tag_list` → `bulk_tag_create_batches` — split paths into `batch_NN.json`.
3. Dispatch parallel Haiku subagents per batch. Each agent calls `bulk_tag_prepare` to pull `existing_tags` + content excerpts for its batch, proposes tags, writes `batch_NN.result.json`.
4. `bulk_tag_verify` each result against its batch (gate).
5. `bulk_tag_aggregate` over the results dir — flatten, alias, drop blocklisted and singleton tags. Surface consolidation candidates (near-duplicate tags).
6. `bulk_tag_consolidate` auto-merges above `confidence_threshold`; below it gets flagged for review.
7. `bulk_tag_apply` with `dry_run=true` first, then a real run.

All classification LLM work is Haiku for cost. The orchestration prompt is in `src/tagger.py`.

## Cache instrumentation

If you want to confirm a cache is doing work in production:

```bash
uv run obsidian-cli cache-stats
```

Returns one JSON blob with hits / misses / sizes / `hit_rate` per cache. `hit_rate` is `null` if there were zero accesses — that means the workload didn't exercise that path, *not* that the cache is broken.

## Contributing

Issues and PRs to <https://github.com/agileresearchservices/hybrid-obsidian-mcp>. Keep changes focused; the project is intentionally small and tries to stay that way.
