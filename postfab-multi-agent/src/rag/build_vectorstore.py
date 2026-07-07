"""
corpus_raw.json을 읽어 ChromaDB에 임베딩 저장.
최초 1회 실행 또는 문서 업데이트 시 실행.
"""
import os
import json
import chromadb
from chromadb.utils import embedding_functions

CORPUS_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "corpus_raw.json")
CHROMA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "chroma")
COLLECTION_NAME = "postfab_knowledge"


def build():
    with open(CORPUS_PATH, encoding="utf-8") as f:
        data = json.load(f)

    docs = data["documents"]
    sections = [{"id": d["id"], "title": d["title"], "content": f"{d['title']}\n{d['text']}"} for d in docs]
    print(f"[RAG] {len(sections)}개 문서 로드 완료")

    ef = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name=os.path.join(os.path.dirname(__file__), "..", "..", "..", "models", "postfab-bge-m3-final")
    )
    client = chromadb.PersistentClient(path=CHROMA_DIR)

    # 기존 컬렉션 초기화 후 재생성
    try:
        client.delete_collection(COLLECTION_NAME)
    except Exception:
        pass
    collection = client.create_collection(COLLECTION_NAME, embedding_function=ef)

    collection.add(
        ids=[s["id"] for s in sections],
        documents=[s["content"] for s in sections],
        metadatas=[{"title": s["title"]} for s in sections],
    )
    print(f"[RAG] ChromaDB 저장 완료 → {CHROMA_DIR}")


if __name__ == "__main__":
    build()
