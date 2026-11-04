"""T12a: generate a new-version PRD (Markdown) that reflects manual changes.

Decisions:
- When: only when the user asks (service/CLI command), never automatically.
- Where: DEFAULT_OUTPUT_DIR (data/generated/); the original input file is only
  read, never written.
- Name: '<name>v<latest version + 1>.md' (PRDv1 -> PRDv2). An existing file or
  an existing (name, version) document is never overwritten.
- Content: the latest document's structure (overview, users, non-functional
  sections, ...) is kept. Requirement blocks (### REQ-xxx title) are rebuilt
  from the current requirements: deleted ones are dropped, edited ones carry
  the new content, manually added ones go after the last requirement block.
  Untouched requirements are written with exactly their stored content.
- The generated document is recorded in `documents` (kind='generated') with
  its chunks. Its chunks are NOT indexed in Chroma (that would duplicate the
  requirement vectors that already exist).
"""
import re
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from chunking.chunking import Chunk, chunk_markdown

DEFAULT_OUTPUT_DIR = Path("data") / "generated"

_REQ_HEADING = re.compile(r"^(REQ-\d+)\s+(.+)$", re.IGNORECASE)


@dataclass
class NewVersionResult:
    document_id: int
    name: str
    version: int
    path: Path
    requirement_count: int


def _current_requirements(conn: sqlite3.Connection) -> dict[str, tuple[str, str]]:
    rows = conn.execute(
        "SELECT req_key, title, content FROM requirements WHERE status = 'active' ORDER BY id"
    ).fetchall()
    return {r["req_key"]: (r["title"], r["content"]) for r in rows}


def _baseline_requirements(conn: sqlite3.Connection, document_id: int) -> dict[str, tuple[str, str]]:
    """Requirements as written in a stored document (read from its chunks)."""
    rows = conn.execute(
        "SELECT heading, content FROM chunks WHERE document_id = ? ORDER BY position",
        (document_id,),
    ).fetchall()
    found = {}
    for r in rows:
        m = _REQ_HEADING.match(r["heading"])
        if m:
            found[m.group(1).upper()] = (m.group(2).strip(), r["content"])
    return found


def _render(chunks: list[Chunk], current: dict[str, tuple[str, str]], old_version: int, new_version: int) -> str:
    last_req = max(
        (i for i, c in enumerate(chunks) if _REQ_HEADING.match(c.heading)), default=None
    )
    parts: list[str] = []
    written: set[str] = set()

    def block(level: int, heading: str, body: str) -> str:
        return f"{'#' * level} {heading}\n\n{body}".rstrip() + "\n"

    for i, c in enumerate(chunks):
        m = _REQ_HEADING.match(c.heading)
        if m:
            key = m.group(1).upper()
            if key in current:  # deleted requirements are simply left out
                title, content = current[key]
                parts.append(block(c.level, f"{key} {title}", content))
                written.add(key)
        else:
            heading, body = c.heading, c.content
            if c.level == 1:
                heading = re.sub(rf"\b[vV]{old_version}\b", f"v{new_version}", heading)
            body = re.sub(
                rf"(\*\*문서 버전:\*\*\s*)v{old_version}\b", rf"\g<1>v{new_version}", body
            )
            body = re.sub(r"(\*\*작성일:\*\*\s*)\S+", rf"\g<1>{date.today().isoformat()}", body)
            parts.append(block(c.level, heading, body))

        if i == last_req:  # manually added requirements go after the last one
            level = c.level
            for key, (title, content) in current.items():
                if key not in written:
                    parts.append(block(level, f"{key} {title}", content))
                    written.add(key)

    if last_req is None:  # no requirement block in the source: append a section
        parts.append("## 요구사항\n")
        for key, (title, content) in current.items():
            parts.append(block(3, f"{key} {title}", content))
            written.add(key)
    return "\n".join(parts)


def _fallback_chunks(name: str, old_version: int) -> list[Chunk]:
    """Minimal structure when the original file is no longer readable."""
    return [
        Chunk(heading=f"{name} v{old_version}", level=1, content=""),
        Chunk(heading="요구사항", level=2, content=""),
    ]


def generate_new_version(
    conn: sqlite3.Connection, output_dir: str | Path = DEFAULT_OUTPUT_DIR
) -> NewVersionResult:
    base = conn.execute(
        "SELECT d.* FROM documents d WHERE d.name = "
        "(SELECT name FROM documents WHERE kind = 'input' ORDER BY id DESC LIMIT 1) "
        "ORDER BY d.version DESC LIMIT 1"
    ).fetchone()
    if base is None:
        raise ValueError("입력된 문서가 없습니다. 먼저 ingest로 문서를 입력하세요.")

    manual = conn.execute(
        "SELECT 1 FROM requirement_history WHERE source_type = 'manual' LIMIT 1"
    ).fetchone()
    current = _current_requirements(conn)
    if manual is None or current == _baseline_requirements(conn, base["id"]):
        raise ValueError(
            f"{base['name']} v{base['version']} 이후 수동으로 바뀐 요구사항이 없어 "
            "새 버전을 만들지 않았습니다."
        )

    name, old_version = base["name"], base["version"]
    new_version = old_version + 1
    if conn.execute(
        "SELECT 1 FROM documents WHERE name = ? AND version = ?", (name, new_version)
    ).fetchone():
        raise ValueError(f"이미 있는 문서 버전입니다: {name} v{new_version}")

    output_dir = Path(output_dir)
    path = output_dir / f"{name}v{new_version}.md"
    if path.exists():
        raise ValueError(f"이미 있는 파일이라 덮어쓰지 않았습니다: {path}")

    source = Path(base["source_path"]) if base["source_path"] else None
    if source is not None and source.is_file():
        chunks = chunk_markdown(source.read_text(encoding="utf-8"), keep_empty=True)
    else:
        chunks = _fallback_chunks(name, old_version)
    text = _render(chunks, current, old_version, new_version)

    new_chunks = chunk_markdown(text)
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        with conn:  # DB row and file are created together
            document_id = conn.execute(
                "INSERT INTO documents (name, version, kind, source_path) "
                "VALUES (?, ?, 'generated', ?)",
                (name, new_version, str(path)),
            ).lastrowid
            conn.executemany(
                "INSERT INTO chunks (document_id, heading, position, content) VALUES (?, ?, ?, ?)",
                [(document_id, c.heading, c.position, c.content) for c in new_chunks],
            )
            path.write_text(text, encoding="utf-8", newline="\n")
    except Exception:
        path.unlink(missing_ok=True)
        raise

    return NewVersionResult(document_id, name, new_version, path, len(current))
