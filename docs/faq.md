# FAQ

## Why hybrid (BM25 + kNN) instead of pure semantic search?

Pure-semantic loses on **exact tokens** — project codenames, customer names, file identifiers, error messages, hash prefixes. The embedding model has no special knowledge that `"Nasuni"` is one thing and not a word it can paraphrase. BM25 nails those queries; kNN nails "I remember the *idea*, not the words" queries. Hybrid gives you both.

The default fusion weights — `LEXICAL_WEIGHT=0.7`, `VECTOR_WEIGHT=0.3` — reflect the reality that for engineering notes the lexical side is usually higher signal. Tune to taste: see [Search Tuning](search-tuning.md).

## Why reranking on top of the hybrid score?

The hybrid pipeline's score is a combination of two **fundamentally different** signals after min-max normalization. It's surprisingly easy for a tag-heavy doc with a strong BM25 hit but weak semantic match to outrank a doc that's a much closer answer to the *question*. The cross-encoder runs `(query, chunk_text)` pairs through a model that was trained to score *relevance*, not similarity — it's the last-mile correction.

It also adds latency (~50ms for a top-10 rerank after the first call, plus ~4s one-time model load). For pure-search use cases on a hot path you can turn it off — `rerank=false` per-query, or `ENABLE_RERANKING=false` globally.

## Why Ollama instead of an OpenAI / Anthropic embedding API?

- **Privacy** — vault contents never leave the machine.
- **Cost** — full reindex of a 600-note vault is ~free-on-laptop instead of a one-time API spend; incremental updates are continuous.
- **Latency** — query embedding is sub-100ms on M-series Macs. No network hop.

Trade-off: `nomic-embed-text` is good but not as strong as the proprietary embedding APIs. For a single-user vault the quality difference rarely shows up in real searches, and the privacy + cost picture is decisive.

## Why does the watcher debounce 10 seconds?

A single Obsidian edit can fire many `modified` events as the editor saves, syncs, and re-touches the file. 10s is long enough to collapse those into one reindex pass, and short enough that searches reflect edits before you've context-switched. The `OPENSEARCH_REFRESH_INTERVAL` default of `5s` is paired with this — searches won't see new docs faster than ~15s no matter what.

If 15s feels too slow, lower both — but the watcher's reindex cost doesn't go down, so this is mostly trading IOPS for freshness.

## Why is `read_note` cached on `mtime_ns` rather than path?

So edits **automatically invalidate** without anyone having to remember to clear the cache. The cache key is `(resolved_path, mtime_ns)` — when the file changes, `mtime_ns` changes, the old key never matches, and the LRU naturally evicts it. `nanosecond` precision means even rapid edits (a save followed immediately by a sync overwrite) produce distinct keys.

## What happens if OpenSearch isn't running?

`obsidian_client.create_client()` lazy-connects on first use. Tool calls return an error string with the underlying connection failure; the MCP server doesn't crash. Bring OpenSearch up (`docker compose up -d`), re-issue the call.

The watcher uses `tenacity` retries on its OpenSearch calls — short outages are absorbed silently; longer outages log to `logs/watcher.log` and the watcher keeps trying.

## Do I need to reindex when I change `.env`?

**Query-time knobs:** no — restart the MCP server / watcher and you're done.

- `VECTOR_WEIGHT`, `LEXICAL_WEIGHT`, `RETRIEVER_K`, `RETRIEVER_FETCH_K`
- `RECENCY_DECAY_*`
- `ENABLE_RERANKING`, `RERANKER_PREWARM`, `RERANKER_CACHE_SIZE`
- `EMBEDDING_QUERY_CACHE_SIZE`, `READ_NOTE_CACHE_SIZE`, `TAXONOMY_CACHE_TTL_SECONDS`

**Index-time knobs:** yes, full reindex (`uv run python -m src.indexer`).

- `OLLAMA_EMBED_MODEL` — different vector space.
- `CHUNK_SIZE`, `CHUNK_OVERLAP` — different chunk boundaries.
- Anything that affects the `chunk_hash` input (parser changes, tag normalization rules).

`OPENSEARCH_REFRESH_INTERVAL` is special — `ensure_index()` syncs it to the existing index via `put_settings`, so it propagates without a recreate.

## The first search after startup is slow (~4s). Why?

The cross-encoder model loads lazily by default — but the MCP server pre-warms it at startup when `RERANKER_PREWARM=true` (which is the default). If the first search is still slow, check:

1. `RERANKER_PREWARM=true` in `.env`.
2. The MCP server log shows a successful prewarm. Prewarm failures are logged and swallowed (the on-demand load path is the fallback).
3. `ENABLE_RERANKING=false` short-circuits the cross-encoder entirely if you want raw hybrid results.

## Can I run this against multiple vaults?

Not currently. `OBSIDIAN_VAULT_PATH`, the OpenSearch index name, and the watcher are all single-tenant. You could clone the repo with a different `.env` and a different `OPENSEARCH_INDEX_NAME` to run a second instance — they'd share the OpenSearch container but use separate indexes — but no part of the code expects this and there's no built-in multiplexing.

## Why is bulk tagging Haiku-only?

Cost. Tagging is a classification problem that doesn't need frontier-model reasoning; Haiku does it well at ~10× lower cost per token. A 600-note run is ~$0.05. See [Bulk Tagging](bulk-tagging.md) for the full pipeline.

## How do I add a new MCP tool?

1. Add the function to the right module — `searcher`, `indexer`, `writer`, `tagger`.
2. Wrap it in `src/server.py` with `@mcp.tool()`. The **docstring is the LLM-facing description**, so write it for the LLM, not for humans — concrete, parameter-by-parameter, with example values.
3. Mirror it as an `obsidian-cli` subcommand in `src/cli.py` if shell access would be useful (it usually is).
4. Add tests under `tests/`.

The same Python function backs both the MCP tool and the CLI — don't fork logic between layers. See [Development](development.md).

## Why isn't this a published package?

It's not packaged for distribution because the configuration surface assumes you'll edit `.env`, the vault paths assume macOS iCloud Obsidian, and the bulk-tag blocklist / aliases are intentionally tailored to one person's taxonomy. Forking and adjusting `src/config.py` + `src/tagger.py` is the supported "make it yours" path.

## Where does the data live?

- **Index data** — a Docker named volume managed by `docker-compose.yml`. `docker compose down` preserves it; `down -v` wipes it.
- **Vault** — `OBSIDIAN_VAULT_PATH`. Never written to except via the documented write tools (`note_create`, `note_append`, `daily_log_*`, `add_todo`, `complete_todo`, `bulk_tag_apply`). All writes go through `writer.py`'s vault-relative path validator — absolute and `~`-prefixed paths are rejected.
- **Logs** — `logs/watcher.log` (stdout) and `logs/watcher.err` (stderr) when started via launchd. The Docker container has its own logs (`docker compose logs opensearch`).
- **Caches** — all in-process, cleared on restart. There is no persistent on-disk cache.

## How do I see what's actually cached?

```bash
uv run obsidian-cli cache-stats
```

Returns a single JSON blob with `hits`, `misses`, `size`, `maxsize`, and `hit_rate` per cache (embedding / reranker / taxonomy / read_note). `hit_rate: null` means zero accesses to that path during the current process lifetime, not that the cache is broken.
