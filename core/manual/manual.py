"""Manual changes to requirements (T9: add; update/delete come in T10/T11).

The requester/modifier check runs first, and every change is written in one
transaction together with its history row (source_type='manual').
"""
import re
import sqlite3

from infra.db.validation import require_actors

_REQ_KEY = re.compile(r"^REQ-\d+$")


def _next_req_key(conn: sqlite3.Connection) -> str:
    # Deleted requirements keep their row (and key), so they are counted too.
    rows = conn.execute("SELECT req_key FROM requirements").fetchall()
    numbers = [int(r["req_key"].split("-")[1]) for r in rows if _REQ_KEY.match(r["req_key"])]
    return f"REQ-{max(numbers, default=0) + 1:03d}"


def add_requirement(
    conn: sqlite3.Connection,
    title: str,
    content: str,
    requester: str,
    modifier: str,
    req_key: str | None = None,
    store=None,
) -> str:
    """Add one requirement by hand and record it in history. Returns its req_key.

    If a Chroma store is given, the new requirement is indexed so searches find
    it; a Chroma failure rolls the SQLite change back.
    """
    requester, modifier = require_actors(requester, modifier)

    title = (title or "").strip()
    content = (content or "").strip()
    if not title or not content:
        raise ValueError("제목(title)과 내용(content)은 필수입니다. 저장하지 않았습니다.")

    if req_key is not None:
        req_key = req_key.strip().upper()
        if not _REQ_KEY.match(req_key):
            raise ValueError(f"요구사항 ID 형식이 올바르지 않습니다(예: REQ-011): {req_key}")
        if conn.execute("SELECT 1 FROM requirements WHERE req_key = ?", (req_key,)).fetchone():
            raise ValueError(f"이미 존재하는 요구사항 ID입니다(삭제된 ID 포함): {req_key}")

    with conn:  # commit on success, rollback on any error
        if req_key is None:
            req_key = _next_req_key(conn)
        requirement_id = conn.execute(
            "INSERT INTO requirements (req_key, title, content) VALUES (?, ?, ?)",
            (req_key, title, content),
        ).lastrowid
        conn.execute(
            "INSERT INTO requirement_history "
            "(requirement_id, action, source_type, new_value, requester, modifier) "
            "VALUES (?, 'add', 'manual', ?, ?, ?)",
            (requirement_id, content, requester, modifier),
        )
        if store is not None:
            from infra.indexing.indexing import upsert_requirement_vector

            upsert_requirement_vector(store, req_key, title, content, requirement_id)
    return req_key


def update_requirement(
    conn: sqlite3.Connection,
    req_key: str,
    content: str,
    requester: str,
    modifier: str,
    store=None,
) -> None:
    """Change the content of an active requirement by hand.

    History keeps the old and new content. If a Chroma store is given, the
    requirement's vector is replaced so searches see the new text; a Chroma
    failure rolls the SQLite change back.
    """
    requester, modifier = require_actors(requester, modifier)

    req_key = (req_key or "").strip().upper()
    content = (content or "").strip()
    if not content:
        raise ValueError("내용(content)은 필수입니다. 저장하지 않았습니다.")

    req = conn.execute("SELECT * FROM requirements WHERE req_key = ?", (req_key,)).fetchone()
    if req is None:
        raise ValueError(f"요구사항을 찾을 수 없습니다: {req_key}")
    if req["status"] != "active":
        raise ValueError(f"삭제된 요구사항은 수정할 수 없습니다: {req_key}")
    if req["content"] == content:
        raise ValueError(f"기존 내용과 같아 수정하지 않았습니다: {req_key}")

    with conn:  # commit only after Chroma succeeded
        conn.execute(
            "UPDATE requirements SET content = ?, updated_at = datetime('now') WHERE id = ?",
            (content, req["id"]),
        )
        conn.execute(
            "INSERT INTO requirement_history "
            "(requirement_id, action, source_type, old_value, new_value, requester, modifier) "
            "VALUES (?, 'update', 'manual', ?, ?, ?, ?)",
            (req["id"], req["content"], content, requester, modifier),
        )
        if store is not None:
            from infra.indexing.indexing import drop_old_vectors, upsert_requirement_vector

            new_id = upsert_requirement_vector(store, req_key, req["title"], content, req["id"])
            drop_old_vectors(store, req_key, keep_id=new_id)


def delete_requirement(
    conn: sqlite3.Connection,
    req_key: str,
    requester: str,
    modifier: str,
    store=None,
) -> None:
    """Mark an active requirement as deleted and record it in history.

    The row stays (status='deleted') so its history and ID are kept. If a
    Chroma store is given, every vector of the requirement is removed; a Chroma
    failure rolls the SQLite change back.
    """
    requester, modifier = require_actors(requester, modifier)

    req_key = (req_key or "").strip().upper()
    req = conn.execute("SELECT * FROM requirements WHERE req_key = ?", (req_key,)).fetchone()
    if req is None:
        raise ValueError(f"요구사항을 찾을 수 없습니다: {req_key}")
    if req["status"] != "active":
        raise ValueError(f"이미 삭제된 요구사항입니다: {req_key}")

    with conn:  # commit only after Chroma succeeded
        conn.execute(
            "UPDATE requirements SET status = 'deleted', updated_at = datetime('now') WHERE id = ?",
            (req["id"],),
        )
        conn.execute(
            "INSERT INTO requirement_history "
            "(requirement_id, action, source_type, old_value, requester, modifier) "
            "VALUES (?, 'delete', 'manual', ?, ?, ?)",
            (req["id"], req["content"], requester, modifier),
        )
        if store is not None:
            from infra.indexing.indexing import drop_old_vectors

            drop_old_vectors(store, req_key)
