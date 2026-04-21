"""Hybrid search over OpenSearch with optional reranking."""

import logging
from typing import Optional

import httpx

from .config import (
    OLLAMA_BASE_URL,
    OLLAMA_EMBED_MODEL,
    OPENSEARCH_INDEX_NAME,
    OPENSEARCH_SEARCH_PIPELINE,
    RETRIEVER_K,
    RETRIEVER_FETCH_K,
)
from .opensearch_client import create_client
from .reranker import get_reranker, ScoredChunk

logger = logging.getLogger(__name__)


def get_embedding(text: str) -> list[float]:
    """Get embedding from Ollama with asymmetric task prefix for nomic-embed-text."""
    response = httpx.post(
        f"{OLLAMA_BASE_URL}/api/embed",
        json={"model": OLLAMA_EMBED_MODEL, "input": f"search_query: {text}"},
        timeout=60.0,
    )
    response.raise_for_status()
    return response.json()["embeddings"][0]


def hybrid_search(
    query: str,
    k: int = RETRIEVER_K,
    fetch_k: int = RETRIEVER_FETCH_K,
    tags: Optional[list[str]] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    folder: Optional[str] = None,
    rerank: bool = True,
) -> list[ScoredChunk]:
    """Execute hybrid search combining kNN vector + BM25 lexical.

    Uses OpenSearch's native hybrid query with search pipeline for
    score normalization and combination.
    """
    client = create_client()
    query_embedding = get_embedding(query)

    # Build filter clauses
    filters = []
    if tags:
        filters.append({"terms": {"tags.keyword": tags}})
    if date_from or date_to:
        date_range = {}
        if date_from:
            date_range["gte"] = date_from
        if date_to:
            date_range["lte"] = date_to
        filters.append({"range": {"date": date_range}})
    if folder:
        filters.append({"prefix": {"folder": folder}})

    # Build kNN query with optional filter
    knn_query = {
        "knn": {
            "embedding": {
                "vector": query_embedding,
                "k": fetch_k,
            }
        }
    }
    if filters:
        knn_query["knn"]["embedding"]["filter"] = {"bool": {"must": filters}}

    # Build BM25 query with optional filter
    bm25_query: dict = {
        "multi_match": {
            "query": query,
            "fields": ["chunk_text", "tags^2", "title^3"],
            "type": "best_fields",
        }
    }
    if filters:
        bm25_query = {
            "bool": {
                "must": [{
                    "multi_match": {
                        "query": query,
                        "fields": ["chunk_text", "tags^2"],
                        "type": "best_fields",
                    }
                }],
                "filter": filters,
            }
        }

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
        logger.warning("Native hybrid search failed, falling back to RRF: %s", e)
        return _rrf_fallback(query, query_embedding, k, fetch_k, filters, rerank)

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

    vector_response = client.search(
        index=OPENSEARCH_INDEX_NAME,
        body={
            "size": fetch_k,
            "_source": {"excludes": ["embedding"]},
            "query": vector_query,
        },
    )

    # Text search
    text_must: dict = {
        "multi_match": {
            "query": query,
            "fields": ["chunk_text", "tags^2", "title^3"],
            "type": "best_fields",
        }
    }
    text_query: dict = {"bool": {"must": [text_must]}}
    if filters:
        text_query["bool"]["filter"] = filters

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
) -> list[ScoredChunk]:
    """Pure BM25 text search (no vector component)."""
    client = create_client()

    filters = []
    if tags:
        filters.append({"terms": {"tags.keyword": tags}})
    if date_from or date_to:
        date_range = {}
        if date_from:
            date_range["gte"] = date_from
        if date_to:
            date_range["lte"] = date_to
        filters.append({"range": {"date": date_range}})
    if folder:
        filters.append({"prefix": {"folder": folder}})

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
) -> list[dict]:
    """List notes matching filters (no full-text search, just metadata filtering)."""
    client = create_client()

    filters = []
    if tags:
        filters.append({"terms": {"tags.keyword": tags}})
    if date_from or date_to:
        date_range = {}
        if date_from:
            date_range["gte"] = date_from
        if date_to:
            date_range["lte"] = date_to
        filters.append({"range": {"date": date_range}})
    if folder:
        filters.append({"prefix": {"folder": folder}})

    query: dict = {"match_all": {}}
    if filters:
        query = {"bool": {"filter": filters}}

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
