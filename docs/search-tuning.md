# Search Tuning

How to nudge relevance without rebuilding the index.

## Hybrid weights

The default fusion is `0.3 * normalized_knn + 0.7 * normalized_bm25`. BM25 wins by design — lexical matches on technical notes (file names, code identifiers, project tags) tend to be high signal, and the semantic side is most useful as a tie-breaker and synonym expander.

| Goal | Knobs |
|---|---|
| Prefer semantic similarity | Raise `VECTOR_WEIGHT`, lower `LEXICAL_WEIGHT` (they don't have to sum to 1, but it's intuitive). |
| Prefer exact phrasing | Inverse — push `LEXICAL_WEIGHT` higher. |
| More recall before rerank | Raise `RETRIEVER_FETCH_K`. The cross-encoder still only sees the top of the list, but it has more to choose from. |

## Exclude tags

Every search entry point accepts an `exclude_tags` argument — a comma-separated string at the MCP/CLI boundary, a `list[str]` in Python. It compiles to a `bool.must_not: { terms: { "tags.keyword": [...] } }` clause that's honored on **both** sides of the hybrid query (the kNN sub-query treats it as a negated filter, since the kNN nested-filter clause only supports `must`) **and** in the RRF fallback path.

```bash
uv run obsidian-cli search "deployment notes" --exclude-tags "archived,draft"
```

```text
search_notes(
  query="deployment notes",
  exclude_tags="archived,draft",
)
```

## Recency decay

By default, hybrid search applies a Gaussian decay on `file_mtime` to the BM25 sub-query. Newer notes get a multiplicative boost; the boost fades smoothly with age.

### The shape

OpenSearch [`function_score`](https://opensearch.org/docs/latest/query-dsl/compound/function-score/) with a `gauss` decay. Applied **only to the BM25 side** of the hybrid query (and to the BM25-equivalent in the RRF fallback). The kNN side is left untouched — wrapping a `knn` query in `function_score` is engine-dependent, and we'd rather change ranking via a knob we fully understand.

```json
{
  "function_score": {
    "query": { "multi_match": { ... } },
    "functions": [{
      "gauss": {
        "file_mtime": {
          "origin": "now",
          "scale": "90d",
          "decay":  0.5
        }
      },
      "weight": 0.3
    }],
    "score_mode": "multiply",
    "boost_mode": "multiply"
  }
}
```

### What that means concretely

- `origin: "now"` — the curve peaks at the current time.
- `scale: "90d"` (default) — at 90 days old, the decay function returns `decay=0.5`.
- `decay: 0.5` — defines what "at the scale" means: half the peak value.
- `weight: 0.3` — scales the decay output before multiplying into BM25, so the boost is bounded in `[0, 0.3]` per doc. A brand-new doc gets `bm25 × 0.3`; a 90-day-old doc gets `bm25 × 0.15`; a 2-year-old doc gets a much smaller multiplier.
- `boost_mode: multiply` + `score_mode: multiply` — function score multiplies the underlying BM25 relevance rather than replacing it, so a strong-relevance old doc can still outrank a weak-relevance new one.

`RECENCY_DECAY_WEIGHT=0` (or `RECENCY_DECAY_ENABLED=false`) makes `_apply_recency_decay` a no-op — the query is returned unwrapped, no `function_score` overhead.

### The important caveat

OpenSearch's `obsidian_hybrid_pipeline` runs each sub-query's hits through a [`normalization-processor`](https://opensearch.org/docs/latest/search-plugins/search-pipelines/normalization-processor/) (min-max within the fetched window) before the weighted-arithmetic-mean combination. That preserves *ordering* inside the BM25 fetch but compresses *score gaps*. The practical effect:

- If recency decay **flips the order** of two BM25 candidates, the flip propagates through.
- If recency decay just **widens the score gap** between two same-ordered docs, normalization mostly squashes it back.
- The **RRF fallback** path doesn't run the pipeline, so it sees the full magnitude shift.

If you want the decay to dominate more aggressively, raise `RECENCY_DECAY_WEIGHT` past 1.0 or shorten `RECENCY_DECAY_SCALE` (e.g. `30d`). If you want it gone entirely, set `RECENCY_DECAY_WEIGHT=0`.

### Why `file_mtime` instead of frontmatter `date`?

`file_mtime` is present on every chunk (set during `_prepare_note_docs` from `Path.stat().st_mtime`). The `date` frontmatter field is optional and inconsistent across the vault. For "what did I touch recently," mtime is the truer signal.

### Tuning intuitions

| Goal | Settings |
|---|---|
| Strongly prefer fresh notes | `RECENCY_DECAY_SCALE=30d`, `RECENCY_DECAY_WEIGHT=0.8` |
| Subtle nudge (default) | `RECENCY_DECAY_SCALE=90d`, `RECENCY_DECAY_WEIGHT=0.3` |
| Archive-friendly (long memory) | `RECENCY_DECAY_SCALE=365d`, `RECENCY_DECAY_WEIGHT=0.2` |
| Disable entirely | `RECENCY_DECAY_WEIGHT=0` |

After changing `.env`, restart the MCP server / watcher. No reindex needed — decay is query-time only.

## Reranking on/off

Cross-encoder reranking (`ms-marco-MiniLM-L-6-v2`) is on by default. It runs after the hybrid fetch, scoring the top-`RERANKER_TOP_K` `(query, chunk)` pairs and re-sorting.

| Symptom | What to try |
|---|---|
| First search is slow (~4s) | Confirm `RERANKER_PREWARM=true`. |
| Want faster dev iteration | Set `RERANKER_PREWARM=false` or `ENABLE_RERANKING=false`. |
| Surprised by rerank changing order | Pass `rerank=false` per-query to see the raw hybrid order. |
| Hit-rate too low | Bump `RERANKER_CACHE_SIZE`; check `cache_stats`. |

## RRF fallback

If the search pipeline is unavailable (older OpenSearch, missing pipeline, error), `searcher` falls back to a manual reciprocal-rank-fusion path that runs kNN and BM25 separately and fuses them in Python. The recency decay and `exclude_tags` filters are both honored on this path. Behaviour is otherwise equivalent except that the pipeline's score normalization isn't applied — the BM25 magnitude shift from recency decay isn't compressed, so the boost has slightly more bite there.
