"""Ingest a Markdown document into SQLite.

Order matters: the requester/modifier check runs first so a bad call costs
nothing (no file read, no LLM call). All writes happen in one transaction, so a
failure leaves the database untouched.

Ingested requirements are recorded in history as action='add',
source_type='auto' (extracted by the agent, not typed in by hand).
"""
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from src.core.chunking.chunking import chunk_markdown
from src.infrastructure.db.validation import require_actors
from src.core.extraction.extraction import extract_requirements


@dataclass
class IngestResult:
    document_id: int
    name: str
    version: int
    chunk_count: int
    requirement_count: int

    def summary(self) -> str:
        return f"{self.name}v{self.version} (청크 {self.chunk_count}, 요구사항 {self.requirement_count})"


def parse_document_name(path: str | Path) -> tuple[str, int]:
    """'PRDv1.md' -> ('PRD', 1). Without a version suffix: (stem, 1)."""
    stem = Path(path).stem
    match = re.match(r"^(.*?)[vV](\d+)$", stem)
    if match and match.group(1).rstrip("_- "):
        return match.group(1).rstrip("_- "), int(match.group(2))
    return stem, 1


def existing_document_error(conn: sqlite3.Connection, name: str, version: int) -> str | None:
    """Message when (name, version) is already stored; None when it is free."""
    row = conn.execute("SELECT kind FROM documents WHERE name = ? AND version = ?", (name, version)).fetchone()
    if row is None:
        return None
    if row["kind"] == "generated":
        return (f"{name} v{version}은 시스템이 수동 변경을 반영해 만든 문서라 같은 버전으로 입력할 수 없습니다. "
                f"파일 이름의 버전을 올려(예: {name}v{version + 1}) 입력하세요.")
    return f"이미 입력된 문서입니다: {name} v{version}"


def _ensure_new(conn: sqlite3.Connection, name: str, version: int, req_keys: list[str]) -> None:
    message = existing_document_error(conn, name, version)
    if message:
        raise ValueError(message)

    existing = []
    for key in req_keys:
        if conn.execute("SELECT 1 FROM requirements WHERE req_key = ?", (key,)).fetchone():
            existing.append(key)
    if existing:
        raise ValueError(
            "이미 저장된 요구사항 ID가 있어 입력하지 않았습니다 "
            f"(새 버전과의 비교는 2차 범위): {', '.join(existing)}"
        )


def ingest_document(
    conn: sqlite3.Connection,
    path: str | Path,
    requester: str,
    modifier: str,
    llm=None,
) -> IngestResult:
    requester, modifier = require_actors(requester, modifier)

    path = Path(path)
    chunks = chunk_markdown(path.read_text(encoding="utf-8"))
    requirements = extract_requirements(chunks, llm=llm)
    if not requirements:
        raise ValueError(f"요구사항을 찾지 못했습니다: {path.name}")

    name, version = parse_document_name(path)
    _ensure_new(conn, name, version, [r.req_key for r in requirements])

    with conn:  # commit on success, rollback on any error
        document_id = conn.execute(
            "INSERT INTO documents (name, version, kind, source_path) VALUES (?, ?, 'input', ?)",
            (name, version, str(path)),
        ).lastrowid

        conn.executemany(
            "INSERT INTO chunks (document_id, heading, position, content) VALUES (?, ?, ?, ?)",
            [(document_id, c.heading, c.position, c.content) for c in chunks],
        )

        for req in requirements:
            requirement_id = conn.execute(
                "INSERT INTO requirements (req_key, title, content, document_id) "
                "VALUES (?, ?, ?, ?)",
                (req.req_key, req.title, req.content, document_id),
            ).lastrowid
            conn.execute(
                "INSERT INTO requirement_history "
                "(requirement_id, action, source_type, new_value, requester, modifier, document_id) "
                "VALUES (?, 'add', 'auto', ?, ?, ?, ?)",
                (requirement_id, req.content, requester, modifier, document_id),
            )

    return IngestResult(
        document_id=document_id,
        name=name,
        version=version,
        chunk_count=len(chunks),
        requirement_count=len(requirements),
    )
