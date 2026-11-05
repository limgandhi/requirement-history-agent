"""SQLite connection and schema. Source of truth for requirements and history."""
import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path("data") / "requirements.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,                 -- e.g. PRDv1
    version     INTEGER NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'input'
                CHECK (kind IN ('input', 'generated')),
    source_path TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (name, version)
);

CREATE TABLE IF NOT EXISTS chunks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id),
    heading     TEXT NOT NULL,
    position    INTEGER NOT NULL,              -- order inside the document
    content     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS requirements (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    req_key     TEXT NOT NULL UNIQUE,          -- e.g. REQ-001
    title       TEXT NOT NULL,
    content     TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active', 'deleted')),
    document_id INTEGER REFERENCES documents(id),
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS requirement_history (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    requirement_id INTEGER NOT NULL REFERENCES requirements(id),
    action         TEXT NOT NULL CHECK (action IN ('add', 'update', 'delete')),
    source_type    TEXT NOT NULL CHECK (source_type IN ('manual', 'auto')),
    old_value      TEXT,
    new_value      TEXT,
    requester      TEXT NOT NULL CHECK (length(trim(requester)) > 0),
    modifier       TEXT NOT NULL CHECK (length(trim(modifier)) > 0),
    document_id    INTEGER REFERENCES documents(id),
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_history_requirement ON requirement_history(requirement_id);
"""

TABLES = ("documents", "chunks", "requirements", "requirement_history")


def connect(path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)  # wait up to 30 s when another writer holds the lock
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()
