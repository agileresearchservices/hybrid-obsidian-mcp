"""Hybrid search over OpenSearch with optional reranking."""

import logging
from typing import Optional

from .config import (
    OPENSEARCH_INDEX_NAME,
    OPENSEARCH_SEARCH_PIPELINE,
    RETRIEVER_K,
    RETRIEVER_FETCH_K,
    RECENCY_DECAY_ENABLED,
    RECENCY_DECAY_SCALE,
    RECENCY_DECAY_WEIGHT,
)
from .embeddings import get_embedding as _embed
from .opensearch_client import create_client
from .reranker import get_reranker, ScoredChunk

logger = logging.getLogger(__name__)


def get_embedding(text: str) -> list[float]:
    """Embed a search query (asymmetric `search_query:` prefix for nomic-embed-text)."""
    return _embed(text, task="search_query")


def _build_filters(
    tags: Optional[list[str]],
    date_from: Optional[str],
    date_to: Optional[str],
    folder: Optional[str],
    exclude_tags: Optional[list[str]],
) -> tuple[list[dict], list[dict]]:
    """Return (must_filters, must_not_filters) for the standard search params."""
    filters: list[dict] = []
    must_not: list[dict] = []
    if tags:
        filters.append({"terms": {"tags.keyword": tags}})
    if date_from or date_to:
        date_range: dict = {}
        if date_from:
            date_range["gte"] = date_from
        if date_to:
            date_range["lte"] = date_to
        filters.append({"range": {"date": date_range}})
    if folder:
        filters.append({"prefix": {"folder": folder}})
    if exclude_tags:
        must_not.append({"terms": {"tags.keyword": exclude_tags}})
    return filters, must_not


def _apply_recency_decay(query: dict) -> dict:
    """Wrap a query in function_score with a gauss decay on file_mtime.

    No-op when disabled or weight=0. Multiplicative boost so a doc with low
    text relevance can't be lifted past a strong-match older doc — the decay
    only adjusts the ordering of similar-scoring results.
    """
    if not RECENCY_DECAY_ENABLED or RECENCY_DECAY_WEIGHT <= 0:
        return query
    return {
        "function_score": {
            "query": query,
            "functions": [
                {
                    "gauss": {
                        "file_mtime": {
                            "origin": "now",
                            "scale": RECENCY_DECAY_SCALE,
                            "decay": 0.5,
                        }
                    },
                    "weight": RECENCY_DECAY_WEIGHT,
                }
            ],
            "score_mode": "multiply",
            "boost_mode": "multiply",
        }
    }


def hybrid_search(
    query: str,
    k: int = RETRIEVER_K,
    fetch_k: int = RETRIEVER_FETCH_K,
    tags: Optional[list[str]] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    folder: Optional[str] = None,
    exclude_tags: Optional[list[str]] = None,
    rerank: bool = True,
) -> list[ScoredChunk]:
    """Execute hybrid search combining kNN vector + BM25 lexical.

    Uses OpenSearch's native hybrid query with search pipeline for
    score normalization and combination.
    """
    client = create_client()
    query_embedding = get_embedding(query)

    filters, must_not = _build_filters(tags, date_from, date_to, folder, exclude_tags)

    # Build kNN query with optional filter (kNN doesn't honor must_not via the
    # nested filter; emulate exclusion by treating must_not as a negated filter).
    knn_filter_clauses = list(filters)
    if must_not:
        knn_filter_clauses.append({"bool": {"must_not": must_not}})

    knn_query = {
        "knn": {
            "embedding": {
                "vector": query_embedding,
                "k": fetch_k,
            }
        }
    }
    if knn_filter_clauses:
        knn_query["knn"]["embedding"]["filter"] = {"bool": {"must": knn_filter_clauses}}

    # Build BM25 query — wrap in function_score for recency decay before any
    # filter wrapping so the decay sees the raw match score.
    bm25_match: dict = {
        "multi_match": {
            "query": query,
            "fields": ["chunk_text", "tags^2", "title^3"],
            "type": "best_fields",
        }
    }
    bm25_with_decay = _apply_recency_decay(bm25_match)

    if filters or must_not:
        bm25_query: dict = {
            "bool": {
                "must": [bm25_with_decay],
                "filter": filters,
                "must_not": must_not,
            }
        }
    else:
        bm25_query = bm25_with_decay

    # Native hybrid query
    body = {
        "size": fetch_k if rerank else k,
        "_source": {"excludes": ["embedding"]},
        "query": {
            "hybrid": {
                "queries": [knn_query, bm25_query]
            }
        },
    }

    try:
        response = client.search(
            index=OPENSEARCH_INDEX_NAME,
            body=body,
            params={"search_pipeline": OPENSEARCH_SEARCH_PIPELINE},
        )
    except Exception as e:
        logger.warning(
            "Native hybrid search failed (%s: %s) — falling back to client-side RRF. "
            "Search pipeline '%s' may be missing or misconfigured; results will use RRF "
            "weighting rather than the configured VECTOR_WEIGHT/LEXICAL_WEIGHT.",
            type(e).__name__,
            e,
            OPENSEARCH_SEARCH_PIPELINE,
        )
        return _rrf_fallback(query, query_embedding, k, fetch_k, filters, must_not, rerank)

    hits = response["hits"]["hits"]
    chunks = [{"_score": hit["_score"], **hit["_source"]} for hit in hits]

    if rerank and chunks:
        return get_reranker().rerank(query, chunks, top_k=k)

    return [
        ScoredChunk(
            chunk_text=c["chunk_text"],
            score=c.get("_score", 0.0),
            metadata={k_: v for k_, v in c.items() if k_ not in ("chunk_text", "embedding", "_score")},
        )
        for c in chunks[:k]
    ]




def _rrf_fallback(
    query: str,
    query_embedding: list[float],
    k: int,
    fetch_k: int,
    filters: list,
    must_not: list,
    rerank: bool,
) -> list[ScoredChunk]:
    """Client-side RRF when native hybrid query isn't available."""
    RRF_K = 60
    client = create_client()

    # Vector search
    knn_body: dict = {
        "knn": {"embedding": {"vector": query_embedding, "k": fetch_k}}
    }
    vector_query: dict = {"bool": {"must": [knn_body]}}
    if filters:
        vector_query["bool"]["filter"] = filters
    if must_not:
        vector_query["bool"]["must_not"] = must_not

    vector_response = client.search(
        index=OPENSEARCH_INDEX_NAME,
        body={
            "size": fetch_k,
            "_source": {"excludes": ["embedding"]},
            "query": vector_query,
        },
    )

    # Text search — wrap the match in function_score so recency decay applies
    # in the fallback path too.
    text_match: dict = {
        "multi_match": {
            "query": query,
            "fields": ["chunk_text", "tags^2", "title^3"],
            "type": "best_fields",
        }
    }
    text_must = _apply_recency_decay(text_match)
    text_query: dict = {"bool": {"must": [text_must]}}
    if filters:
        text_query["bool"]["filter"] = filters
    if must_not:
        text_query["bool"]["must_not"] = must_not

    text_response = client.search(
        index=OPENSEARCH_INDEX_NAME,
        body={
            "size": fetch_k,
            "_source": {"excludes": ["embedding"]},
            "query": text_query,
        },
    )

    # Compute RRF scores
    vector_ranks = {hit["_id"]: (rank, hit) for rank, hit in enumerate(vector_response["hits"]["hits"], 1)}
    text_ranks = {hit["_id"]: (rank, hit) for rank, hit in enumerate(text_response["hits"]["hits"], 1)}

    all_ids = set(vector_ranks.keys()) | set(text_ranks.keys())
    scored = []
    for doc_id in all_ids:
        v_rank = vector_ranks[doc_id][0] if doc_id in vector_ranks else 999999
        t_rank = text_ranks[doc_id][0] if doc_id in text_ranks else 999999
        rrf_score = (0.5 / (RRF_K + v_rank)) + (0.5 / (RRF_K + t_rank))
        hit = vector_ranks.get(doc_id, text_ranks.get(doc_id))[1]
        scored.append((rrf_score, hit))

    # Sort by RRF score (primary), then by file_mtime (recency tie-breaker)
    scored.sort(key=lambda x: (x[0], x[1]["_source"].get("file_mtime", 0)), reverse=True)
    top = scored[: fetch_k if rerank else k]

    chunks = [{"_score": score, **hit["_source"]} for score, hit in top]

    if rerank and chunks:
        return get_reranker().rerank(query, chunks, top_k=k)

    return [
        ScoredChunk(
            chunk_text=c["chunk_text"],
            score=c.get("_score", 0.0),
            metadata={k_: v for k_, v in c.items() if k_ not in ("chunk_text", "embedding", "_score")},
        )
        for c in chunks[:k]
    ]


def keyword_search(
    query: str,
    k: int = RETRIEVER_K,
    tags: Optional[list[str]] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    folder: Optional[str] = None,
    exclude_tags: Optional[list[str]] = None,
) -> list[ScoredChunk]:
    """Pure BM25 text search (no vector component)."""
    client = create_client()

    filters, must_not = _build_filters(tags, date_from, date_to, folder, exclude_tags)

    must = [{
        "multi_match": {
            "query": query,
            "fields": ["chunk_text", "tags^2", "title^3"],
            "type": "best_fields",
        }
    }]
    body_query: dict = {"bool": {"must": must}}
    if filters:
        body_query["bool"]["filter"] = filters
    if must_not:
        body_query["bool"]["must_not"] = must_not

    response = client.search(
        index=OPENSEARCH_INDEX_NAME,
        body={
            "size": k,
            "_source": {"excludes": ["embedding"]},
            "query": body_query,
            "sort": [
                {"_score": {"order": "desc"}},
                {"file_mtime": {"order": "desc", "missing": "_last"}},
            ],
        },
    )

    return [
        ScoredChunk(
            chunk_text=hit["_source"]["chunk_text"],
            score=hit["_score"],
            metadata={k_: v for k_, v in hit["_source"].items() if k_ not in ("chunk_text", "embedding")},
        )
        for hit in response["hits"]["hits"]
    ]


def list_notes(
    folder: Optional[str] = None,
    tags: Optional[list[str]] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 50,
    exclude_tags: Optional[list[str]] = None,
) -> list[dict]:
    """List notes matching filters (no full-text search, just metadata filtering)."""
    client = create_client()

    filters, must_not = _build_filters(tags, date_from, date_to, folder, exclude_tags)

    query: dict = {"match_all": {}}
    if filters or must_not:
        bool_body: dict = {}
        if filters:
            bool_body["filter"] = filters
        if must_not:
            bool_body["must_not"] = must_not
        query = {"bool": bool_body}

    response = client.search(
        index=OPENSEARCH_INDEX_NAME,
        body={
            "size": limit,
            "_source": ["title", "date", "tags", "folder", "file_path", "doc_type"],
            "query": query,
            "collapse": {"field": "document_id"},
            "sort": [{"file_mtime": {"order": "desc", "missing": "_last"}}],
        },
    )

    return [hit["_source"] for hit in response["hits"]["hits"]]


def graph_neighbors(target: str, hops: int = 1, limit: int = 20) -> list[dict]:
    """Find notes 1–2 wikilink hops from `target` (the link-text of a note).

    v1 matches on link text (the canonical Obsidian semantic), not on file path —
    `[[KMW]]` matches notes that contain that wikilink regardless of which `.md`
    file Obsidian would actually resolve it to. The target is normalized
    (section anchor stripped, lowercased, whitespace collapsed).

    Returns a deduped list of notes (collapsed by document_id) with the
    `hop_distance` from the target.
    """
    from .vault_parser import normalize_wikilink

    if hops not in (1, 2):
        raise ValueError("graph_neighbors supports hops=1 or hops=2")

    client = create_client()
    norm_target = normalize_wikilink(target)
    if not norm_target:
        return []

    # Hop 1: notes that link directly to the target.
    hop1 = client.search(
        index=OPENSEARCH_INDEX_NAME,
        body={
            "size": limit if hops == 1 else 200,
            "_source": ["title", "date", "tags", "folder", "file_path", "doc_type", "wikilinks"],
            "query": {"term": {"wikilinks": norm_target}},
            "collapse": {"field": "document_id"},
            "sort": [{"file_mtime": {"order": "desc", "missing": "_last"}}],
        },
    )
    hop1_notes = [hit["_source"] for hit in hop1["hits"]["hits"]]
    hop1_titles = {normalize_wikilink(n.get("title", "")) for n in hop1_notes}

    results = [{**n, "hop_distance": 1} for n in hop1_notes]

    if hops == 1:
        return results[:limit]

    # Hop 2: outgoing links from hop-1 notes, minus the target and the hop-1 set.
    hop1_outgoing: set[str] = set()
    for n in hop1_notes:
        for link in n.get("wikilinks", []) or []:
            hop1_outgoing.add(link)
    hop1_outgoing.discard(norm_target)
    hop1_outgoing -= hop1_titles
    if not hop1_outgoing:
        return results[:limit]

    hop2 = client.search(
        index=OPENSEARCH_INDEX_NAME,
        body={
            "size": limit,
            "_source": ["title", "date", "tags", "folder", "file_path", "doc_type"],
            "query": {"terms": {"wikilinks": list(hop1_outgoing)}},
            "collapse": {"field": "document_id"},
            "sort": [{"file_mtime": {"order": "desc", "missing": "_last"}}],
        },
    )
    seen_paths = {n.get("file_path") for n in hop1_notes}
    for hit in hop2["hits"]["hits"]:
        src = hit["_source"]
        if src.get("file_path") in seen_paths:
            continue
        seen_paths.add(src.get("file_path"))
        results.append({**src, "hop_distance": 2})

    return results[:limit]


if __name__ == "__main__":
    import sys

    query = " ".join(sys.argv[1:]) or "what happened with Nasuni?"
    print(f"\nSearching: '{query}'\n")

    results = hybrid_search(query, k=5)
    for i, r in enumerate(results, 1):
        title = r.metadata.get("title", "?")
        date = r.metadata.get("date", "?")
        score = r.score
        print(f"{i}. [{score:.3f}] {title} ({date})")
        print(f"   {r.chunk_text[:150]}...")
        print()
