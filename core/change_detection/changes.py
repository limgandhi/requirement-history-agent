"""T24/T25: read a new version of a document, find what changed, and record it.

Order: the requester/modifier check runs first. SQLite and Chroma are changed
together: every write sits in one transaction and the vectors are written last,
so a Chroma failure rolls the SQLite change back.

Detected changes are recorded in history as source_type='auto' with the new
document as their source. The requirement rows keep their original source
document (requirements.document_id); only added requirements point to the new one.
"""
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from core.change_detection.detection import Detection, Item, detect_changes
from core.chunking.chunking import Chunk, chunk_markdown
from core.extraction.extraction import Requirement, extract_requirements
from core.ingest.ingest import parse_document_name
from infra.db.validation import require_actors


@dataclass
class Plan:
    """A new document version read and compared, not saved yet."""
    name: str
    version: int
    chunks: list[Chunk]
    requirements: list[Requirement]
    detection: Detection


@dataclass
class ChangeResult:
    document_id: int
    name: str
    version: int
    chunk_count: int
    detection: Detection

    @property
    def requirement_count(self) -> int:
        d = self.detection
        return len(d.added) + len(d.changed) + len(d.unchanged)  # active requirements after the change

    def summary(self) -> str:
        return format_detection(self.detection, f"{self.name}v{self.version}")


def format_detection(d: Detection, label: str = "") -> str:
    lines = [f"{label} 변경 감지: 추가 {len(d.added)} / 변경 {len(d.changed)} / 삭제 {len(d.deleted)} / 그대로 {len(d.unchanged)}".strip()]
    for c in d.added:
        note = f" (문서의 {c.doc_key} → 새 ID)" if c.doc_key else ""
        lines.append(f"- 추가 {c.req_key} {c.title}{note}")
    for c in d.changed:
        lines.append(f"- 변경 {c.req_key} {c.title}")
    for c in d.deleted:
        lines.append(f"- 삭제 {c.req_key} {c.title}")
    if d.id_less:
        lines.append("※ 문서에 요구사항 ID가 없어 모두 추가로 처리했고, 삭제는 감지하지 않았습니다.")
    return "\n".join(lines)


def latest_version(conn: sqlite3.Connection, name: str) -> int | None:
    return conn.execute("SELECT MAX(version) FROM documents WHERE name = ?", (name,)).fetchone()[0]


def is_new_version(conn: sqlite3.Connection, path: str | Path) -> bool:
    """True when a document with this name exists and the file has a higher version."""
    name, version = parse_document_name(path)
    top = latest_version(conn, name)
    return top is not None and version > top


def _active_in_scope(conn: sqlite3.Connection, name: str) -> dict[str, tuple[str, str]]:
    """Active requirements that belong to this document name (plus hand-added ones)."""
    rows = conn.execute(
        "SELECT r.req_key, r.title, r.content FROM requirements r "
        "LEFT JOIN documents d ON d.id = r.document_id "
        "WHERE r.status = 'active' AND (d.name = ? OR r.document_id IS NULL) ORDER BY r.id",
        (name,),
    ).fetchall()
    return {r["req_key"]: (r["title"], r["content"]) for r in rows}


def plan_new_version(conn: sqlite3.Connection, path: str | Path, llm=None) -> Plan:
    """Read the file and compare it with the current requirements. Writes nothing."""
    path = Path(path)
    name, version = parse_document_name(path)
    top = latest_version(conn, name)
    if top is None:
        raise ValueError(f"{name} 문서가 아직 없어 새 버전으로 비교할 수 없습니다. 처음 입력으로 처리하세요.")
    if version <= top:
        raise ValueError(
            f"이미 {name} v{top}까지 있어 v{version}은 새 버전으로 입력할 수 없습니다."
            if version < top else f"이미 입력된 문서입니다: {name} v{version}"
        )

    chunks = chunk_markdown(path.read_text(encoding="utf-8"))
    requirements = extract_requirements(chunks, llm=llm)
    if not requirements:
        raise ValueError(f"요구사항을 찾지 못했습니다: {path.name}")

    id_less = all(r.position is None for r in requirements)  # LLM-extracted: its IDs are not the document's
    items = []
    for r in requirements:
        info = r.change_info
        actors = (info["requester"], info["modifier"]) if info else None
        items.append(Item(None if id_less else r.req_key, r.title, r.content, actors))

    known = {row[0] for row in conn.execute("SELECT req_key FROM requirements")}
    detection = detect_changes(_active_in_scope(conn, name), known, items, id_less)
    return Plan(name, version, chunks, requirements, detection)


def apply_plan(conn: sqlite3.Connection, plan: Plan, path: str | Path, requester: str, modifier: str, store=None) -> ChangeResult:
    requester, modifier = require_actors(requester, modifier)
    d = plan.detection
    if not d.has_changes:
        raise ValueError(f"{plan.name} v{plan.version}에는 바뀐 요구사항이 없어 저장하지 않았습니다.")

    with conn:  # commit only after Chroma succeeded
        document_id = conn.execute(
            "INSERT INTO documents (name, version, kind, source_path) VALUES (?, ?, 'input', ?)",
            (plan.name, plan.version, str(path)),
        ).lastrowid
        conn.executemany(
            "INSERT INTO chunks (document_id, heading, position, content) VALUES (?, ?, ?, ?)",
            [(document_id, c.heading, c.position, c.content) for c in plan.chunks],
        )

        def history(requirement_id, action, old, new, change):
            who_requested, who_modified = change.actors or (requester, modifier)
            conn.execute(
                "INSERT INTO requirement_history (requirement_id, action, source_type, old_value, new_value, "
                "requester, modifier, document_id) VALUES (?, ?, 'auto', ?, ?, ?, ?, ?)",
                (requirement_id, action, old, new, who_requested, who_modified, document_id),
            )

        vectors = []  # (change, requirement_id) to write to Chroma at the end
        for c in d.added:
            rid = conn.execute(
                "INSERT INTO requirements (req_key, title, content, document_id) VALUES (?, ?, ?, ?)",
                (c.req_key, c.title, c.content, document_id),
            ).lastrowid
            history(rid, "add", None, c.content, c)
            vectors.append((c, rid))
        for c in d.changed:
            rid = conn.execute("SELECT id FROM requirements WHERE req_key = ?", (c.req_key,)).fetchone()["id"]
            conn.execute(
                "UPDATE requirements SET title = ?, content = ?, updated_at = datetime('now') WHERE id = ?",
                (c.title, c.content, rid),
            )
            if c.old_content.strip() == c.content.strip():  # only the title changed
                old, new = f"[제목] {c.old_title}", f"[제목] {c.title}"
            else:
                old, new = c.old_content, c.content
            history(rid, "update", old, new, c)
            vectors.append((c, rid))
        for c in d.deleted:
            rid = conn.execute("SELECT id FROM requirements WHERE req_key = ?", (c.req_key,)).fetchone()["id"]
            conn.execute(
                "UPDATE requirements SET status = 'deleted', updated_at = datetime('now') WHERE id = ?", (rid,))
            history(rid, "delete", c.old_content, None, c)

        if store is not None:
            from infra.indexing.indexing import drop_old_vectors, upsert_requirement_vector

            source = {"document_id": document_id, "document_name": plan.name, "version": plan.version}
            for c, rid in vectors:
                vector_id = upsert_requirement_vector(store, c.req_key, c.title, c.content, rid, source=source)
                drop_old_vectors(store, c.req_key, keep_id=vector_id)  # the old text must not stay searchable
            for c in d.deleted:
                drop_old_vectors(store, c.req_key)

    return ChangeResult(document_id, plan.name, plan.version, len(plan.chunks), d)


def ingest_new_version(conn: sqlite3.Connection, path: str | Path, requester: str, modifier: str, llm=None, store=None) -> ChangeResult:
    requester, modifier = require_actors(requester, modifier)  # before reading the file or calling the LLM
    plan = plan_new_version(conn, path, llm=llm)
    return apply_plan(conn, plan, path, requester, modifier, store=store)
