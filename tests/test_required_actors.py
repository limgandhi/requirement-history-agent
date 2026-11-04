"""T12: requester/modifier are required on every write path.

Bad calls must be rejected before anything happens: no SQL, no file read, no
LLM call, no Chroma call, no DB file.
"""
from pathlib import Path

import pytest

from core.service.service import ingest_and_index
from infra.db.db import TABLES, connect, init_schema
from infra.db.validation import require_actors
from core.ingest.ingest import ingest_document
from core.manual.manual import add_requirement, delete_requirement, update_requirement

SAMPLES = Path(__file__).resolve().parent.parent / "samples"

BLANKS = ["", " ", "\t\n", "　", None, 123]


# ---- unit: the single validation function ----
@pytest.mark.parametrize("blank", BLANKS)
def test_blank_requester_is_rejected(blank):
    with pytest.raises(ValueError, match="요청자"):
        require_actors(blank, "kim")


@pytest.mark.parametrize("blank", BLANKS)
def test_blank_modifier_is_rejected(blank):
    with pytest.raises(ValueError, match="수정자"):
        require_actors("lee", blank)


def test_both_missing_names_both():
    with pytest.raises(ValueError) as e:
        require_actors("", None)
    assert "요청자" in str(e.value) and "수정자" in str(e.value)


def test_valid_actors_are_stripped():
    assert require_actors("  lee ", "kim\n") == ("lee", "kim")


# ---- every write path rejects before doing anything ----
class SpyConnection:
    """Records every SQL statement; any use counts as 'touched the DB'."""

    def __init__(self):
        self.calls = []

    def execute(self, *args, **kwargs):
        self.calls.append(args)
        raise AssertionError("DB must not be touched when actors are missing")

    executemany = execute

    def __enter__(self):
        self.calls.append("enter")
        raise AssertionError("DB must not be touched when actors are missing")

    def __exit__(self, *exc):
        return False


class ForbiddenLLM:
    def with_structured_output(self, schema):
        raise AssertionError("LLM must not be called when actors are missing")


class ForbiddenStore:
    def __getattr__(self, name):
        raise AssertionError("Chroma must not be touched when actors are missing")


# Every function that writes requirements/history. When a new write path is
# added, add it here so it is covered by the same checks.
def _ingest(conn, requester, modifier):
    ingest_document(conn, SAMPLES / "does_not_exist.md", requester, modifier, llm=ForbiddenLLM())


def _add(conn, requester, modifier):
    add_requirement(conn, "제목", "내용", requester, modifier, req_key="REQ-001")


def _update(conn, requester, modifier):
    update_requirement(conn, "REQ-001", "새 내용", requester, modifier, store=ForbiddenStore())


def _delete(conn, requester, modifier):
    delete_requirement(conn, "REQ-001", requester, modifier, store=ForbiddenStore())


WRITE_PATHS = [_ingest, _add, _update, _delete]
ACTOR_PAIRS = [
    ("", "kim"), ("lee", ""), ("", ""), (None, "kim"), ("lee", None),
    ("  ", "kim"), ("lee", "\t"), (123, "kim"),
]


@pytest.mark.parametrize("path", WRITE_PATHS, ids=lambda f: f.__name__.strip("_"))
@pytest.mark.parametrize("requester,modifier", ACTOR_PAIRS)
def test_write_path_rejects_before_touching_anything(path, requester, modifier):
    spy = SpyConnection()
    with pytest.raises(ValueError, match="필수"):
        path(spy, requester, modifier)
    assert spy.calls == []


@pytest.mark.parametrize("path", WRITE_PATHS, ids=lambda f: f.__name__.strip("_"))
def test_write_path_leaves_real_db_empty(path, tmp_path):
    conn = connect(tmp_path / "t.db")
    init_schema(conn)
    with pytest.raises(ValueError, match="필수"):
        path(conn, "", "")
    assert sum(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES) == 0
    conn.close()


@pytest.mark.parametrize("requester,modifier", ACTOR_PAIRS)
def test_service_ingest_rejects_without_creating_db(tmp_path, requester, modifier):
    db = tmp_path / "never.db"
    with pytest.raises(ValueError, match="필수"):
        ingest_and_index(db, tmp_path / "chroma", SAMPLES / "PRDv1.md", requester, modifier,
                         llm=ForbiddenLLM(), store=ForbiddenStore())
    assert not db.exists()
    assert not (tmp_path / "chroma").exists()


# ---- last line of defence: the DB itself refuses blank actors in history ----
@pytest.mark.parametrize("requester,modifier", [("", "kim"), ("lee", " "), (None, "kim")])
def test_db_constraint_rejects_blank_actors_in_history(tmp_path, requester, modifier):
    import sqlite3

    conn = connect(tmp_path / "t.db")
    init_schema(conn)
    conn.execute("INSERT INTO requirements (req_key, title, content) VALUES ('REQ-001', 't', 'c')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO requirement_history (requirement_id, action, source_type, requester, modifier) "
            "VALUES (1, 'add', 'manual', ?, ?)",
            (requester, modifier),
        )
    conn.close()
