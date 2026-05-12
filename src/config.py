"""Configuration for Hybrid Obsidian MCP server."""

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root
load_dotenv(Path(__file__).parent.parent / ".env")

# Obsidian vault
OBSIDIAN_VAULT_PATH = os.getenv(
    "OBSIDIAN_VAULT_PATH",
    str(Path.home() / "Library/Mobile Documents/iCloud~md~obsidian/Documents/obsidian-vault"),
)

# OpenSearch
OPENSEARCH_HOST = os.getenv("OPENSEARCH_HOST", "localhost")
OPENSEARCH_PORT = int(os.getenv("OPENSEARCH_PORT", 9201))
OPENSEARCH_INDEX_NAME = os.getenv("OPENSEARCH_INDEX_NAME", "obsidian_notes")
OPENSEARCH_SEARCH_PIPELINE = os.getenv("OPENSEARCH_SEARCH_PIPELINE", "obsidian_hybrid_pipeline")
OPENSEARCH_TIMEOUT = int(os.getenv("OPENSEARCH_TIMEOUT", 30))

# Ollama
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")

# Vector config
VECTOR_DIMENSION = 768  # nomic-embed-text output

# Reranker
RERANKER_MODEL = os.getenv("RERANKER_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")
ENABLE_RERANKING = os.getenv("ENABLE_RERANKING", "true").lower() == "true"
RERANKER_TOP_K = int(os.getenv("RERANKER_TOP_K", 10))

# Search defaults
RETRIEVER_K = int(os.getenv("RETRIEVER_K", 10))
RETRIEVER_FETCH_K = int(os.getenv("RETRIEVER_FETCH_K", 40))
VECTOR_WEIGHT = float(os.getenv("VECTOR_WEIGHT", 0.3))
LEXICAL_WEIGHT = float(os.getenv("LEXICAL_WEIGHT", 0.7))

# Chunking
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", 1000))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", 200))

# Recency decay (applied to BM25 sub-query of hybrid + fallback)
# weight=0 disables the boost; scale is OpenSearch date-math (e.g. "90d", "30d").
RECENCY_DECAY_ENABLED = os.getenv("RECENCY_DECAY_ENABLED", "true").lower() == "true"
RECENCY_DECAY_SCALE = os.getenv("RECENCY_DECAY_SCALE", "90d")
RECENCY_DECAY_WEIGHT = float(os.getenv("RECENCY_DECAY_WEIGHT", 0.3))
