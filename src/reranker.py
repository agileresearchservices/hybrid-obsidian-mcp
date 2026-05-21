"""Cross-encoder reranker using sentence-transformers for local reranking."""

import hashlib
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Optional

from .config import ENABLE_RERANKING, RERANKER_CACHE_SIZE, RERANKER_MODEL

logger = logging.getLogger(__name__)


@dataclass
class ScoredChunk:
    """A search result chunk with its reranker score."""

    chunk_text: str
    score: float
    metadata: dict


@dataclass
class CacheInfo:
    hits: int = 0
    misses: int = 0
    maxsize: int = 0
    currsize: int = 0


class _BoundedScoreCache:
    """LRU keyed on (query_hash, chunk_hash) -> float. maxsize=0 disables caching."""

    def __init__(self, maxsize: int):
        self._maxsize = maxsize
        self._data: "OrderedDict[tuple[str, str], float]" = OrderedDict()
        self._hits = 0
        self._misses = 0

    def get(self, key: tuple[str, str]) -> Optional[float]:
        if self._maxsize == 0:
            self._misses += 1
            return None
        if key in self._data:
            self._data.move_to_end(key)
            self._hits += 1
            return self._data[key]
        self._misses += 1
        return None

    def put(self, key: tuple[str, str], value: float) -> None:
        if self._maxsize == 0:
            return
        self._data[key] = value
        self._data.move_to_end(key)
        while len(self._data) > self._maxsize:
            self._data.popitem(last=False)

    def clear(self) -> None:
        self._data.clear()
        self._hits = 0
        self._misses = 0

    def info(self) -> CacheInfo:
        return CacheInfo(
            hits=self._hits,
            misses=self._misses,
            maxsize=self._maxsize,
            currsize=len(self._data),
        )


def _hash_query(query: str) -> str:
    return hashlib.sha256(query.encode("utf-8")).hexdigest()


class LocalReranker:
    """Cross-encoder reranker using sentence-transformers.

    Uses ms-marco-MiniLM-L-6-v2 by default - a lightweight model
    purpose-built for passage reranking. Fully local, no API calls.

    Scores are memoized per (sha256(query), chunk_hash) so repeated reranks
    over overlapping candidate sets skip the cross-encoder forward pass.
    """

    def __init__(self, model_name: str = RERANKER_MODEL):
        self._model = None
        self._model_name = model_name
        self._cache = _BoundedScoreCache(RERANKER_CACHE_SIZE)

    def _ensure_model(self):
        """Lazy-load the model on first use."""
        if self._model is None:
            from sentence_transformers import CrossEncoder
            logger.info("Loading reranker model: %s", self._model_name)
            start = time.time()
            self._model = CrossEncoder(self._model_name)
            logger.info("Reranker loaded in %.1fs", time.time() - start)

    def rerank(
        self,
        query: str,
        chunks: list[dict],
        top_k: int = 10,
    ) -> list[ScoredChunk]:
        """Rerank search result chunks by relevance to query.

        Args:
            query: The search query
            chunks: List of dicts with 'chunk_text' and metadata fields
            top_k: Number of top results to return

        Returns:
            List of ScoredChunk sorted by relevance score descending
        """
        if not chunks:
            return []

        if not ENABLE_RERANKING:
            return [
                ScoredChunk(
                    chunk_text=c["chunk_text"],
                    score=c.get("_score", 0.0),
                    metadata={k: v for k, v in c.items() if k not in ("chunk_text", "embedding")},
                )
                for c in chunks[:top_k]
            ]

        query_hash = _hash_query(query)

        # Partition into (already-scored from cache) and (need-to-score).
        # Chunks lacking chunk_hash bypass the cache entirely and are always scored.
        scores: list[Optional[float]] = [None] * len(chunks)
        misses_idx: list[int] = []
        for i, chunk in enumerate(chunks):
            chash = chunk.get("chunk_hash")
            if chash:
                cached = self._cache.get((query_hash, chash))
                if cached is not None:
                    scores[i] = cached
                    continue
            misses_idx.append(i)

        if misses_idx:
            self._ensure_model()
            pairs = [(query, chunks[i]["chunk_text"]) for i in misses_idx]
            predicted = self._model.predict(pairs)
            for i, raw_score in zip(misses_idx, predicted):
                score = float(raw_score)
                scores[i] = score
                chash = chunks[i].get("chunk_hash")
                if chash:
                    self._cache.put((query_hash, chash), score)

        scored = [
            ScoredChunk(
                chunk_text=chunk["chunk_text"],
                score=score,
                metadata={k: v for k, v in chunk.items() if k not in ("chunk_text", "embedding")},
            )
            for chunk, score in zip(chunks, scores)
        ]
        scored.sort(key=lambda x: x.score, reverse=True)
        return scored[:top_k]


# Module-level singleton
_reranker: Optional[LocalReranker] = None


def get_reranker() -> LocalReranker:
    """Get or create the singleton reranker instance."""
    global _reranker
    if _reranker is None:
        _reranker = LocalReranker()
    return _reranker


def clear_reranker_cache() -> None:
    """Clear the in-process per-pair reranker score cache."""
    if _reranker is not None:
        _reranker._cache.clear()


def reranker_cache_info() -> CacheInfo:
    """Return CacheInfo (hits, misses, maxsize, currsize) for the reranker cache."""
    if _reranker is None:
        return CacheInfo(maxsize=RERANKER_CACHE_SIZE)
    return _reranker._cache.info()
