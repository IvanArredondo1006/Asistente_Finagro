import logging
from functools import lru_cache
from pathlib import Path
from typing import Dict, List

import chromadb
from chromadb.utils.embedding_functions import OpenAIEmbeddingFunction

from app.config import (
    OPENAI_API_KEY,
    OPENAI_EMBEDDING_MODEL,
    RAG_COLLECTION_NAME,
    RAG_INDEX_PATH,
    RAG_TOP_K,
)

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _get_collection():
    if not OPENAI_API_KEY:
        logger.warning("OPENAI_API_KEY no esta configurada; el indice semantico no estara disponible")
        return None
    embedding_fn = OpenAIEmbeddingFunction(api_key=OPENAI_API_KEY, model_name=OPENAI_EMBEDDING_MODEL)
    storage_path = Path(RAG_INDEX_PATH).expanduser()
    storage_path.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(storage_path))
    try:
        collection = client.get_collection(name=RAG_COLLECTION_NAME, embedding_function=embedding_fn)
    except chromadb.errors.InvalidArgumentError:
        collection = client.get_or_create_collection(name=RAG_COLLECTION_NAME, embedding_function=embedding_fn)
    return collection


def retrieve_facts(query: str, limit: int | None = None) -> List[Dict[str, object]]:
    collection = _get_collection()
    if collection is None:
        return []
    top_k = limit or RAG_TOP_K
    if top_k <= 0:
        return []
    try:
        result = collection.query(query_texts=[query], n_results=top_k)
    except Exception as exc:
        logger.warning("Fallo consultando el indice semantico: %s", exc)
        return []
    documents = result.get("documents") or []
    metadatas = result.get("metadatas") or []
    if not documents or not documents[0]:
        return []
    payload: List[Dict[str, object]] = []
    for doc, metadata in zip(documents[0], metadatas[0]):
        payload.append({
            "text": doc,
            "metadata": metadata or {},
        })
    return payload
