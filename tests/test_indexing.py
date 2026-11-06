"""T6: index chunks into a new Chroma collection linked to SQLite."""
from pathlib import Path

import pytest

from src.infrastructure.db.db import connect, init_schema
from src.infrastructure.indexing.indexing import get_vectorstore, index_document, search_chunks
from src.core.ingest.ingest import ingest_document

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


@pytest.fixture(scope="module")
def indexed(tmp_path_factory):
    """Ingest + index PRDv1 once (REQ headings -> no extraction LLM call)."""
    base = tmp_path_factory.mktemp("t6")
    conn = connect(base / "t6.db")
    init_schema(conn)
    result = ingest_document(conn, SAMPLES / "PRDv1.md", "lee", "kim")
    store = get_vectorstore(persist_dir=base / "chroma")
    count = index_document(conn, result.document_id, store)
    yield conn, store, result, count
    conn.close()


def test_collection_count_equals_chunk_count(indexed):
    conn, store, result, count = indexed
    assert count == result.chunk_count
    assert store._collection.count() == result.chunk_count


def test_search_voting_period_ranks_req003_first(indexed):
    _, store, _, _ = indexed
    hits = search_chunks("투표 기간", k=1, store=store)
    assert hits[0].req_key == "REQ-003"


def test_metadata_links_back_to_sqlite(indexed):
    conn, store, result, _ = indexed
    hit = search_chunks("투표 기간", k=1, store=store)[0]
    row = conn.execute("SELECT * FROM chunks WHERE id = ?", (hit.chunk_id,)).fetchone()
    assert row["heading"] == hit.heading
    assert (hit.document_name, hit.version) == ("PRD", 1)


def test_reindex_does_not_duplicate(indexed):
    conn, store, result, _ = indexed
    index_document(conn, result.document_id, store)
    assert store._collection.count() == result.chunk_count
