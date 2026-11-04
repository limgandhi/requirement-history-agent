"""Chroma index over SQLite chunks (SQLite is the source of truth).

Each chunk is stored with id 'chunk-<chunks.id>' and metadata that links back
to SQLite (chunk_id, document_id, ...). Re-indexing a document overwrites the
same ids, so it never creates duplicates.
"""
import re
import sqlite3
from dataclasses import dataclass

from langchain_chroma import Chroma

from config import config
from llm.llm import get_embeddings

_REQ_KEY = re.compile(r"^(REQ-\d+)\b", re.IGNORECASE)


@dataclass
class SearchHit:
    chunk_id: int
    document_id: int
    document_name: str
    version: int
    heading: str
    req_key: str | None
    content: str
    score: float  # cosine distance: lower is more similar


def get_vectorstore(persist_dir: str = config.CHROMA_DIR, embeddings=None) -> Chroma:
    return Chroma(
        collection_name=config.CHROMA_COLLECTION,
        embedding_function=embeddings or get_embeddings(),
        persist_directory=str(persist_dir),
        collection_metadata={"hnsw:space": "cosine"},
    )


def index_document(conn: sqlite3.Connection, document_id: int, store: Chroma | None = None) -> int:
    rows = conn.execute(
        "SELECT c.id, c.heading, c.position, c.content, d.name, d.version "
        "FROM chunks c JOIN documents d ON d.id = c.document_id "
        "WHERE c.document_id = ? ORDER BY c.position",
        (document_id,),
    ).fetchall()
    if not rows:
        return 0

    store = store or get_vectorstore()
    texts, metadatas, ids = [], [], []
    for r in rows:
        meta = {
            "chunk_id": r["id"],
            "document_id": document_id,
            "document_name": r["name"],
            "version": r["version"],
            "heading": r["heading"],
            "position": r["position"],
        }
        match = _REQ_KEY.match(r["heading"])
        if match:  # Chroma rejects None values, so the key is omitted otherwise
            meta["req_key"] = match.group(1).upper()
        texts.append(f"{r['heading']}\n{r['content']}")
        metadatas.append(meta)
        ids.append(f"chunk-{r['id']}")

    store.add_texts(texts=texts, metadatas=metadatas, ids=ids)
    return len(ids)


def search_chunks(query: str, k: int = 4, store: Chroma | None = None) -> list[SearchHit]:
    store = store or get_vectorstore()
    hits = []
    for doc, score in store.similarity_search_with_score(query, k=k):
        m = doc.metadata
        hits.append(
            SearchHit(
                chunk_id=m["chunk_id"],
                document_id=m["document_id"],
                document_name=m["document_name"],
                version=m["version"],
                heading=m["heading"],
                req_key=m.get("req_key"),
                content=doc.page_content,
                score=score,
            )
        )
    return hits
