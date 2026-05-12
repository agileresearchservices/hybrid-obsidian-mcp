"""Shared Ollama embeddings client with retry + batching.

Both indexer (search_document) and searcher (search_query) go through here
so the wire format, timeout, and retry policy live in one place.
"""

import logging
from typing import Literal

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .config import OLLAMA_BASE_URL, OLLAMA_EMBED_MODEL

logger = logging.getLogger(__name__)

Task = Literal["search_query", "search_document"]

DEFAULT_BATCH_SIZE = 32
HTTP_TIMEOUT = 60.0


class EmbeddingError(RuntimeError):
    """Raised when Ollama embedding fails after retries."""


_RETRYABLE = (
    httpx.TimeoutException,
    httpx.NetworkError,
    httpx.RemoteProtocolError,
)


def _prefix(text: str, task: Task) -> str:
    return f"{task}: {text}"


@retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, min=0.5, max=4.0),
    retry=retry_if_exception_type(_RETRYABLE),
)
def _post(payload: dict) -> dict:
    response = httpx.post(
        f"{OLLAMA_BASE_URL}/api/embed",
        json=payload,
        timeout=HTTP_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def get_embedding(text: str, task: Task = "search_query") -> list[float]:
    """Embed a single string. Applies the nomic asymmetric task prefix."""
    data = _post({"model": OLLAMA_EMBED_MODEL, "input": _prefix(text, task)})
    embeddings = data.get("embeddings") or []
    if not embeddings:
        raise EmbeddingError(f"Ollama returned no embeddings for task={task}")
    return embeddings[0]


def get_embeddings_batch(
    texts: list[str],
    task: Task = "search_document",
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> list[list[float]]:
    """Embed many strings, batching to Ollama's array-input endpoint.

    Returns a list aligned 1:1 with `texts`. If a batch fails after retries,
    the exception propagates — callers decide how to recover.
    """
    if not texts:
        return []
    out: list[list[float]] = []
    for start in range(0, len(texts), batch_size):
        chunk = texts[start : start + batch_size]
        prefixed = [_prefix(t, task) for t in chunk]
        data = _post({"model": OLLAMA_EMBED_MODEL, "input": prefixed})
        embeddings = data.get("embeddings") or []
        if len(embeddings) != len(chunk):
            raise EmbeddingError(
                f"Ollama returned {len(embeddings)} embeddings for {len(chunk)} inputs"
            )
        out.extend(embeddings)
    return out
