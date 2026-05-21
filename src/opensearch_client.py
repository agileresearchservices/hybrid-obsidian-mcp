"""OpenSearch client, index mapping, and search pipeline setup."""

import logging

from opensearchpy import OpenSearch

from .config import (
    OPENSEARCH_HOST,
    OPENSEARCH_PORT,
    OPENSEARCH_INDEX_NAME,
    OPENSEARCH_REFRESH_INTERVAL,
    OPENSEARCH_SEARCH_PIPELINE,
    OPENSEARCH_TIMEOUT,
    VECTOR_DIMENSION,
    VECTOR_WEIGHT,
    LEXICAL_WEIGHT,
)

logger = logging.getLogger(__name__)

INDEX_MAPPING = {
    "settings": {
        "index": {
            "number_of_shards": 1,
            "number_of_replicas": 0,
            "knn": True,
            "knn.algo_param.ef_search": 100,
            "refresh_interval": OPENSEARCH_REFRESH_INTERVAL,
        },
        "analysis": {
            "analyzer": {
                "english_analyzer": {
                    "tokenizer": "standard",
                    "filter": ["lowercase", "stop", "snowball"],
                }
            }
        },
    },
    "mappings": {
        "properties": {
            "embedding": {
                "type": "knn_vector",
                "dimension": VECTOR_DIMENSION,
                "method": {
                    "name": "hnsw",
                    "space_type": "cosinesimil",
                    "engine": "lucene",
                    "parameters": {"ef_construction": 512, "m": 16},
                },
            },
            "chunk_text": {"type": "text", "analyzer": "english_analyzer"},
            "title": {
                "type": "text",
                "fields": {"keyword": {"type": "keyword"}},
            },
            "date": {
                "type": "date",
                "format": "yyyy-MM-dd||yyyy-MM-dd'T'HH:mm:ss.SSSZ||epoch_millis",
            },
            "file_mtime": {
                "type": "date",
                "format": "epoch_millis",
            },
            "tags": {
                "type": "text",
                "analyzer": "english_analyzer",
                "fields": {"keyword": {"type": "keyword"}},
            },
            "folder": {"type": "keyword"},
            "file_path": {"type": "keyword"},
            "document_id": {"type": "keyword"},
            "chunk_index": {"type": "integer"},
            "chunk_hash": {"type": "keyword"},
            "doc_type": {"type": "keyword"},
        }
    },
}

SEARCH_PIPELINE = {
    "description": "Hybrid search: min-max normalization + weighted arithmetic mean",
    "phase_results_processors": [
        {
            "normalization-processor": {
                "normalization": {"technique": "min_max"},
                "combination": {
                    "technique": "arithmetic_mean",
                    "parameters": {"weights": [VECTOR_WEIGHT, LEXICAL_WEIGHT]},
                },
            }
        }
    ],
}


def create_client() -> OpenSearch:
    """Create an OpenSearch client."""
    return OpenSearch(
        hosts=[{"host": OPENSEARCH_HOST, "port": OPENSEARCH_PORT}],
        use_ssl=False,
        verify_certs=False,
        timeout=OPENSEARCH_TIMEOUT,
        retry_on_timeout=True,
        max_retries=3,
    )


def ensure_index(client: OpenSearch) -> None:
    """Create index and search pipeline if they don't exist."""
    if not client.indices.exists(index=OPENSEARCH_INDEX_NAME):
        client.indices.create(index=OPENSEARCH_INDEX_NAME, body=INDEX_MAPPING)
        logger.info("Created index '%s'", OPENSEARCH_INDEX_NAME)
    else:
        logger.info("Index '%s' already exists", OPENSEARCH_INDEX_NAME)
        # Best-effort additive mapping update for fields added after initial create.
        # Missing fields on old docs are harmless (cache miss, not error).
        try:
            client.indices.put_mapping(
                index=OPENSEARCH_INDEX_NAME,
                body={"properties": {
                    "chunk_hash": {"type": "keyword"},
                }},
            )
        except Exception as e:
            logger.debug("put_mapping(chunk_hash) skipped: %s", e)
        # Sync refresh_interval setting so config changes propagate to existing indexes.
        try:
            client.indices.put_settings(
                index=OPENSEARCH_INDEX_NAME,
                body={"index": {"refresh_interval": OPENSEARCH_REFRESH_INTERVAL}},
            )
        except Exception as e:
            logger.debug("put_settings(refresh_interval) skipped: %s", e)

    try:
        client.transport.perform_request(
            "PUT",
            f"/_search/pipeline/{OPENSEARCH_SEARCH_PIPELINE}",
            body=SEARCH_PIPELINE,
        )
        logger.info("Search pipeline '%s' created/updated", OPENSEARCH_SEARCH_PIPELINE)
    except Exception as e:
        logger.warning("Could not create search pipeline: %s", e)
