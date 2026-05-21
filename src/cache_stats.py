"""Aggregator for the in-process caches used across the MCP server.

Keeps the per-cache info functions independent (each cache module owns its
own shape) while presenting a single uniform snapshot for ops tooling.
"""

from __future__ import annotations

from typing import Any, Optional

from . import embeddings, reranker, tagger


def _hit_rate(hits: int, misses: int) -> Optional[float]:
    total = hits + misses
    if total == 0:
        return None
    return round(hits / total, 4)


def collect_cache_stats() -> dict[str, dict[str, Any]]:
    """Snapshot all in-process cache states as a single dict.

    Each cache reports `hits`, `misses`, `hit_rate` (None when no traffic),
    plus whatever shape fields make sense for that cache (LRUs expose
    maxsize/currsize; the taxonomy TTL cache exposes ttl_seconds/age_seconds).
    """
    emb = embeddings.embedding_cache_info()
    rer = reranker.reranker_cache_info()
    tax = tagger.taxonomy_cache_info()
    rn = tagger.read_note_cache_info()
    return {
        "embedding_query": {
            "hits": emb.hits,
            "misses": emb.misses,
            "maxsize": emb.maxsize,
            "currsize": emb.currsize,
            "hit_rate": _hit_rate(emb.hits, emb.misses),
        },
        "reranker_scores": {
            "hits": rer.hits,
            "misses": rer.misses,
            "maxsize": rer.maxsize,
            "currsize": rer.currsize,
            "hit_rate": _hit_rate(rer.hits, rer.misses),
        },
        "taxonomy": {
            "hits": tax.hits,
            "misses": tax.misses,
            "ttl_seconds": tax.ttl_seconds,
            "age_seconds": tax.age_seconds,
            "size": tax.size,
            "hit_rate": _hit_rate(tax.hits, tax.misses),
        },
        "read_note": {
            "hits": rn.hits,
            "misses": rn.misses,
            "maxsize": rn.maxsize,
            "currsize": rn.currsize,
            "hit_rate": _hit_rate(rn.hits, rn.misses),
        },
    }
