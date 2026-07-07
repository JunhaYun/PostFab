"""
ChromaDB에서 쿼리와 유사한 문서를 검색하는 Retriever.
"""
import os
import chromadb
from chromadb.utils import embedding_functions

CHROMA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "chroma")
COLLECTION_NAME = "postfab_knowledge"

_collection = None


def _get_collection():
    global _collection
    if _collection is None:
        ef = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=os.path.join(os.path.dirname(__file__), "..", "..", "..", "models", "postfab-bge-m3-final")
        )
        client = chromadb.PersistentClient(path=CHROMA_DIR)
        _collection = client.get_collection(COLLECTION_NAME, embedding_function=ef)
    return _collection


def retrieve(query: str, n_results: int = 3) -> list[dict]:
    """
    쿼리와 유사한 문서 청크를 반환.
    Returns: [{"title": ..., "content": ..., "distance": ...}, ...]
    """
    col = _get_collection()
    results = col.query(query_texts=[query], n_results=n_results)
    docs = []
    for doc, meta, dist in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ):
        docs.append({
            "title": meta.get("title", ""),
            "content": doc,
            "distance": round(dist, 4),
        })
    return docs


def retrieve_as_context(query: str, n_results: int = 3) -> str:
    """검색 결과를 LLM 컨텍스트용 문자열로 반환."""
    docs = retrieve(query, n_results)
    if not docs:
        return "관련 지식을 찾을 수 없습니다."
    parts = []
    for d in docs:
        parts.append(f"[{d['title']}]\n{d['content']}")
    return "\n\n---\n\n".join(parts)
