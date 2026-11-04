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
    chunk_id: int | None  # None for vectors written from a manual change
    document_id: int | None
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


def upsert_requirement_vector(store: Chroma, req_key: str, title: str, content: str, requirement_id: int) -> str:
    """Write the current text of a requirement as vector 'req-<id>'; same id overwrites.

    Used for manual changes. Chunk vectors of the original document are left
    untouched here; call drop_old_vectors() after this succeeds.
    """
    vector_id = f"req-{requirement_id}"
    heading = f"{req_key} {title}"
    store.add_texts(
        texts=[f"{heading}\n{content}"],
        metadatas=[{"req_key": req_key, "heading": heading, "document_name": "manual", "version": 0}],
        ids=[vector_id],
    )
    return vector_id


def drop_old_vectors(store: Chroma, req_key: str, keep_id: str | None = None) -> None:
    """Delete every vector of a requirement except keep_id (None: delete all)."""
    ids = [i for i in store.get(where={"req_key": req_key})["ids"] if i != keep_id]
    if ids:
        store.delete(ids=ids)


def search_chunks(query: str, k: int = 4, store: Chroma | None = None) -> list[SearchHit]:
    store = store or get_vectorstore()
    hits = []
    for doc, score in store.similarity_search_with_score(query, k=k):
        m = doc.metadata
        hits.append(
            SearchHit(
                chunk_id=m.get("chunk_id"),
                document_id=m.get("document_id"),
                document_name=m.get("document_name", "manual"),
                version=m.get("version", 0),
                heading=m["heading"],
                req_key=m.get("req_key"),
                content=doc.page_content,
                score=score,
            )
        )
    return hits
