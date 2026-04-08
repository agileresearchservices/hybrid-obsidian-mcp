"""Cross-encoder reranker using sentence-transformers for local reranking."""

import logging
import time
from dataclasses import dataclass
from typing import Optional

from .config import RERANKER_MODEL, ENABLE_RERANKING

logger = logging.getLogger(__name__)


@dataclass
class ScoredChunk:
    """A search result chunk with its reranker score."""

    chunk_text: str
    score: float
    metadata: dict


class LocalReranker:
    """Cross-encoder reranker using sentence-transformers.

    Uses ms-marco-MiniLM-L-6-v2 by default - a lightweight model
    purpose-built for passage reranking. Fully local, no API calls.
    """

    def __init__(self, model_name: str = RERANKER_MODEL):
        self._model = None
        self._model_name = model_name

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

        self._ensure_model()

        pairs = [(query, c["chunk_text"]) for c in chunks]
        scores = self._model.predict(pairs)

        scored = []
        for chunk, score in zip(chunks, scores):
            scored.append(ScoredChunk(
                chunk_text=chunk["chunk_text"],
                score=float(score),
                metadata={k: v for k, v in chunk.items() if k not in ("chunk_text", "embedding")},
            ))

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
