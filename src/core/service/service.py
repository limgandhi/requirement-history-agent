"""Use cases shared by every front end. The CLI only parses args and prints."""
import threading
from functools import wraps
from pathlib import Path

from src.infrastructure.db.db import connect, init_schema
from src.infrastructure.db.validation import require_actors
from src.infrastructure.indexing.indexing import get_vectorstore, index_document
from src.core.change_detection.changes import (
    ChangeResult, format_detection, ingest_new_version, is_new_version, latest_version, plan_new_version)
from src.core.ingest.ingest import IngestResult, ingest_document, parse_document_name
from src.core.manual.manual import add_requirement, delete_requirement, update_requirement
from src.core.manual.new_version import DEFAULT_OUTPUT_DIR, NewVersionResult, generate_new_version
from src.core.qa.alternatives import format_candidates, similar_requirements
from src.core.qa.qa import Answer, ask


# Writes run one at a time inside this process (SQLite and Chroma are changed together, and the
# next requirement number / change detection must not race). Waiting for the user's answer
# happens outside the service, so the lock is only held while a write runs.
_WRITE_LOCK = threading.RLock()


def _serialized(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        with _WRITE_LOCK:
            return fn(*args, **kwargs)
    return wrapper


def exclusive():
    """Hold the write lock across several service calls (e.g. a change and the new version
    that must contain exactly that change). The lock is reentrant, so the calls inside may
    take it again."""
    return _WRITE_LOCK


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


@_serialized
def ingest_and_index(db_path, chroma_dir, path, requester, modifier, llm=None, store=None) -> IngestResult | ChangeResult:
    """First input of a document, or -- for a higher version of a known document -- change detection."""
    # Validate first so a bad call does not even create the DB file.
    requester, modifier = require_actors(requester, modifier)
    conn = connect(db_path)
    try:
        init_schema(conn)
        if is_new_version(conn, path):
            store = store or get_vectorstore(persist_dir=chroma_dir)
            return ingest_new_version(conn, path, requester, modifier, llm=llm, store=store)
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


def preview_ingest(db_path, path, llm=None) -> str | None:
    """What inputting this file would change: a summary for a higher version of a known
    document, None for a first input. Raises the same errors the input itself would."""
    if not Path(db_path).exists():
        return None
    conn = connect(db_path)
    try:
        init_schema(conn)
        name, _ = parse_document_name(path)
        if latest_version(conn, name) is None:
            return None
        plan = plan_new_version(conn, path, llm=llm)  # rejects versions that are not higher
        if not plan.detection.has_changes:
            raise ValueError(f"{plan.name} v{plan.version}에는 바뀐 요구사항이 없어 저장하지 않았습니다.")
        return format_detection(plan.detection, f"{plan.name}v{plan.version}")
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


@_serialized
def add_requirement_manual(db_path, chroma_dir, title, content, requester, modifier, store=None) -> str:
    require_actors(requester, modifier)  # reject before touching Chroma
    return _manual_change(
        db_path, chroma_dir,
        lambda conn, s: add_requirement(conn, title, content, requester, modifier, store=s), store)


@_serialized
def update_requirement_manual(db_path, chroma_dir, req_key, content, requester, modifier, store=None) -> None:
    require_actors(requester, modifier)
    _manual_change(
        db_path, chroma_dir,
        lambda conn, s: update_requirement(conn, req_key, content, requester, modifier, store=s), store)


@_serialized
def delete_requirement_manual(db_path, chroma_dir, req_key, requester, modifier, store=None) -> None:
    require_actors(requester, modifier)
    _manual_change(
        db_path, chroma_dir,
        lambda conn, s: delete_requirement(conn, req_key, requester, modifier, store=s), store)


@_serialized
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


# ---- read-only views (a front end lists these; nothing here writes) ----

def _read(db_path, fn):
    if not Path(db_path).exists():
        return []
    conn = connect(db_path)
    try:
        init_schema(conn)
        return fn(conn)
    finally:
        conn.close()


def list_requirements(db_path) -> list[dict]:
    """Every requirement (active first, then deleted) with its current text."""
    return _read(db_path, lambda conn: [dict(r) for r in conn.execute(
        "SELECT req_key, title, content, status, updated_at FROM requirements "
        "ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END, id")])


def requirement_history(db_path, req_key: str) -> list[dict]:
    """History of one requirement, oldest first, with who changed it and from which document."""
    def run(conn):
        rows = conn.execute(
            "SELECT h.action, h.source_type, h.old_value, h.new_value, h.requester, h.modifier, h.created_at, "
            "COALESCE(d.name, rd.name, '수동 입력') AS document, COALESCE(d.version, rd.version) AS version "
            "FROM requirement_history h JOIN requirements r ON r.id = h.requirement_id "
            "LEFT JOIN documents d ON d.id = h.document_id LEFT JOIN documents rd ON rd.id = r.document_id "
            "WHERE r.req_key = ? ORDER BY h.id", ((req_key or "").upper(),)).fetchall()
        return [dict(r) for r in rows]
    return _read(db_path, run)


def list_documents(db_path) -> list[dict]:
    """Input documents and the new versions the system generated (newest first)."""
    return _read(db_path, lambda conn: [dict(r) for r in conn.execute(
        "SELECT id, name, version, kind, source_path, created_at FROM documents ORDER BY id DESC")])
