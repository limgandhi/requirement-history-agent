"""Use cases shared by every front end. The CLI only parses args and prints."""
from pathlib import Path

from db.db import connect, init_schema
from db.validation import require_actors
from indexing.indexing import get_vectorstore, index_document
from ingest.ingest import IngestResult, ingest_document
from manual.new_version import DEFAULT_OUTPUT_DIR, NewVersionResult, generate_new_version
from qa.qa import Answer, ask


def _undo_ingest(conn, store, document_id: int) -> None:
    """Remove a just-ingested document so SQLite and Chroma stay consistent."""
    with conn:
        conn.execute(
            "DELETE FROM requirement_history WHERE requirement_id IN "
            "(SELECT id FROM requirements WHERE document_id = ?)", (document_id,))
        conn.execute("DELETE FROM requirements WHERE document_id = ?", (document_id,))
        conn.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))
        conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))
    try:
        store.delete(where={"document_id": document_id})
    except Exception:
        pass


def ingest_and_index(db_path, chroma_dir, path, requester, modifier, llm=None, store=None) -> IngestResult:
    # Validate first so a bad call does not even create the DB file.
    requester, modifier = require_actors(requester, modifier)
    conn = connect(db_path)
    try:
        init_schema(conn)
        result = ingest_document(conn, path, requester, modifier, llm=llm)
        store = store or get_vectorstore(persist_dir=chroma_dir)
        try:
            index_document(conn, result.document_id, store)
        except Exception:
            _undo_ingest(conn, store, result.document_id)
            raise
        return result
    finally:
        conn.close()


def ask_question(db_path, chroma_dir, question, llm=None, store=None) -> Answer:
    if not Path(db_path).exists():
        raise ValueError("DB가 없습니다. 먼저 ingest로 문서를 입력하세요.")
    conn = connect(db_path)
    try:
        init_schema(conn)
        store = store or get_vectorstore(persist_dir=chroma_dir)
        return ask(conn, question, store=store, llm=llm)
    finally:
        conn.close()


def create_new_version(db_path, output_dir=DEFAULT_OUTPUT_DIR) -> NewVersionResult:
    """Write the manual-change-reflecting PRD as a new file (user-triggered)."""
    if not Path(db_path).exists():
        raise ValueError("DB가 없습니다. 먼저 ingest로 문서를 입력하세요.")
    conn = connect(db_path)
    try:
        init_schema(conn)
        return generate_new_version(conn, output_dir)
    finally:
        conn.close()
