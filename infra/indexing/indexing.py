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
from infra.llm.llm import get_embeddings

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
    score: float  # merged rank score of the hybrid search: higher is better


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


def upsert_requirement_vector(
    store: Chroma, req_key: str, title: str, content: str, requirement_id: int, source: dict | None = None
) -> str:
    """Write the current text of a requirement as vector 'req-<id>'; same id overwrites.

    Used for manual changes. Chunk vectors of the original document are left
    untouched here; call drop_old_vectors() after this succeeds.
    """
    vector_id = f"req-{requirement_id}"
    heading = f"{req_key} {title}"
    store.add_texts(
        texts=[f"{heading}\n{content}"],
        metadatas=[{"req_key": req_key, "heading": heading, "document_name": "manual", "version": 0, **(source or {})}],
        ids=[vector_id],
    )
    return vector_id


def drop_old_vectors(store: Chroma, req_key: str, keep_id: str | None = None) -> None:
    """Delete every vector of a requirement except keep_id (None: delete all)."""
    ids = [i for i in store.get(where={"req_key": req_key})["ids"] if i != keep_id]
    if ids:
        store.delete(ids=ids)


# ---- hybrid search (T16; method from practices/day2_practice/hybrid.py) ----
# BM25 over Korean morphemes (keywords) + vector search (meaning), merged by weighted
# reciprocal rank fusion, the same scheme as LangChain's EnsembleRetriever.
BM25_WEIGHT = 0.3
VECTOR_WEIGHT = 0.7
_RRF_C = 60
_FETCH = 20  # candidates taken from each ranking before merging
_KEEP_TAGS = {"NNG", "NNP", "NNB", "NR", "NP", "VV", "VA", "SL", "SN"}
_kiwi = None


def korean_tokens(text: str) -> list[str]:
    """Nouns, stems, numbers and foreign words only (same filter as day2 hybrid.py)."""
    global _kiwi
    if _kiwi is None:
        from kiwipiepy import Kiwi  # heavy import, loaded on first search

        _kiwi = Kiwi()
    return [t.form for t in _kiwi.tokenize(text) if t.tag in _KEEP_TAGS]


def _to_hit(content: str, m: dict, score: float) -> SearchHit:
    return SearchHit(
        chunk_id=m.get("chunk_id"),
        document_id=m.get("document_id"),
        document_name=m.get("document_name", "manual"),
        version=m.get("version", 0),
        heading=m["heading"],
        req_key=m.get("req_key"),
        content=content,
        score=score,
    )


def _vector_ranking(store: Chroma, query: str) -> list[tuple[str, SearchHit]]:
    out = []
    for doc, distance in store.similarity_search_with_score(query, k=_FETCH):
        if doc.metadata.get("req_key"):  # only requirement chunks can be sources
            out.append((doc.page_content, _to_hit(doc.page_content, doc.metadata, distance)))
    return out


def _bm25_ranking(store: Chroma, query: str) -> list[tuple[str, SearchHit]]:
    """BM25 over the requirement texts currently stored in Chroma (so manual edits count)."""
    from rank_bm25 import BM25Okapi

    data = store.get(include=["documents", "metadatas"])
    items = [(d, m) for d, m in zip(data.get("documents") or [], data.get("metadatas") or [])
             if m and m.get("req_key")]
    query_tokens = korean_tokens(query)
    if not items or not query_tokens:
        return []
    bm25 = BM25Okapi([korean_tokens(d) or ["_"] for d, _ in items])
    scores = bm25.get_scores(query_tokens)
    ranked = sorted(zip(scores, items), key=lambda x: x[0], reverse=True)[:_FETCH]
    return [(d, _to_hit(d, m, float(sc))) for sc, (d, m) in ranked if sc > 0]


def search_chunks(query: str, k: int = 4, store: Chroma | None = None, hybrid: bool = True) -> list[SearchHit]:
    """Top-k requirement chunks. hybrid=False uses the vector ranking only.

    Only chunks of requirements (those with a req_key) are returned: general sections
    such as the overview can never be a source, so they must not take a slot.
    score is the merged rank score (higher is better).
    """
    store = store or get_vectorstore()
    rankings = [(VECTOR_WEIGHT if hybrid else 1.0, _vector_ranking(store, query))]
    if hybrid:
        rankings.append((BM25_WEIGHT, _bm25_ranking(store, query)))

    merged: dict[str, list] = {}  # key -> [score, hit]
    for weight, ranking in rankings:
        for rank, (content, hit) in enumerate(ranking, 1):
            key = f"{hit.req_key}|{hit.chunk_id}|{content}"
            entry = merged.setdefault(key, [0.0, hit])
            entry[0] += weight / (_RRF_C + rank)
    best = sorted(merged.values(), key=lambda e: e[0], reverse=True)[:k]
    for score, hit in best:
        hit.score = score
    return [hit for _, hit in best]
