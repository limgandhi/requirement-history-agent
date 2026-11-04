"""T2: schema creation and basic constraints."""
import sqlite3

import pytest

from infra.db.db import TABLES, connect, init_schema


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "test.db")
    init_schema(c)
    yield c
    c.close()


def test_all_tables_exist(conn):
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    names = {r["name"] for r in rows}
    assert set(TABLES) <= names


def test_init_schema_is_idempotent(conn):
    init_schema(conn)


def test_insert_and_read_requirement(conn):
    conn.execute(
        "INSERT INTO requirements (req_key, title, content) VALUES (?, ?, ?)",
        ("REQ-001", "회원 가입", "이메일로 가입한다"),
    )
    row = conn.execute("SELECT * FROM requirements WHERE req_key='REQ-001'").fetchone()
    assert row["title"] == "회원 가입"
    assert row["status"] == "active"


def _add_requirement(conn):
    cur = conn.execute(
        "INSERT INTO requirements (req_key, title, content) VALUES ('REQ-001', 't', 'c')"
    )
    return cur.lastrowid


@pytest.mark.parametrize("requester,modifier", [("", "kim"), ("lee", ""), ("  ", "kim")])
def test_history_rejects_empty_actor(conn, requester, modifier):
    rid = _add_requirement(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO requirement_history "
            "(requirement_id, action, source_type, requester, modifier) "
            "VALUES (?, 'add', 'manual', ?, ?)",
            (rid, requester, modifier),
        )


def test_history_accepts_valid_actor(conn):
    rid = _add_requirement(conn)
    conn.execute(
        "INSERT INTO requirement_history "
        "(requirement_id, action, source_type, requester, modifier) "
        "VALUES (?, 'add', 'manual', 'lee', 'kim')",
        (rid,),
    )
    assert conn.execute("SELECT COUNT(*) FROM requirement_history").fetchone()[0] == 1
