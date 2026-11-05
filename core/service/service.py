"""Use cases shared by every front end. The CLI only parses args and prints."""
from pathlib import Path

from infra.db.db import connect, init_schema
from infra.db.validation import require_actors
from infra.indexing.indexing import get_vectorstore, index_document
from core.ingest.ingest import IngestResult, ingest_document
from core.manual.manual import add_requirement, delete_requirement, update_requirement
from core.manual.new_version import DEFAULT_OUTPUT_DIR, NewVersionResult, generate_new_version
from core.qa.alternatives import format_candidates, similar_requirements
from core.qa.qa import Answer, ask


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


def check_actors(requester, modifier) -> None:
    """Reject empty requester/modifier early (before asking the user to confirm)."""
    require_actors(requester, modifier)


def find_similar_requirements(db_path, chroma_dir, question, store=None) -> str:
    """Text listing the closest current requirements (an alternative when nothing is found)."""
    if not Path(db_path).exists():
        raise ValueError("DB가 없습니다. 먼저 ingest로 문서를 입력하세요.")
    conn = connect(db_path)
    try:
        init_schema(conn)
        store = store or get_vectorstore(persist_dir=chroma_dir)
        return format_candidates(similar_requirements(conn, question, store=store))
    finally:
        conn.close()


def _manual_change(db_path, chroma_dir, change, store=None):
    """Run one manual change with the Chroma store, after actor validation."""
    if not Path(db_path).exists():
        raise ValueError("DB가 없습니다. 먼저 ingest로 문서를 입력하세요.")
    conn = connect(db_path)
    try:
        init_schema(conn)
        store = store or get_vectorstore(persist_dir=chroma_dir)
        return change(conn, store)
    finally:
        conn.close()


def add_requirement_manual(db_path, chroma_dir, title, content, requester, modifier, store=None) -> str:
    require_actors(requester, modifier)  # reject before touching Chroma
    return _manual_change(
        db_path, chroma_dir,
        lambda conn, s: add_requirement(conn, title, content, requester, modifier, store=s), store)


def update_requirement_manual(db_path, chroma_dir, req_key, content, requester, modifier, store=None) -> None:
    require_actors(requester, modifier)
    _manual_change(
        db_path, chroma_dir,
        lambda conn, s: update_requirement(conn, req_key, content, requester, modifier, store=s), store)


def delete_requirement_manual(db_path, chroma_dir, req_key, requester, modifier, store=None) -> None:
    require_actors(requester, modifier)
    _manual_change(
        db_path, chroma_dir,
        lambda conn, s: delete_requirement(conn, req_key, requester, modifier, store=s), store)


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
