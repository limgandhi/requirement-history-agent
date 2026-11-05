"""T35: many people saving at once must not lose, duplicate or corrupt anything.

Each save is: ask (pending) -> answer yes -> change saved + a new version file written.
"""
import sqlite3
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from test_web_api import make_client

USERS = 8
ADD = {"title": "동시 추가", "content": "동시에 추가된 요구사항", "requester": "park", "modifier": "choi"}


def _save(app_client, n):
    client = TestClient(app_client.app, raise_server_exceptions=False)  # one client per user
    sid = f"user-session-{n:03d}"
    asked = client.post("/api/chat", json={"session_id": sid, "message": "추가해줘"})
    q = asked.json()["pending"][0]
    done = client.post("/api/resume", json={"session_id": sid, "answers": {q["id"]: True}})
    return asked.status_code, done.status_code, done.json()


def test_simultaneous_saves_all_succeed_without_duplicates(tmp_path):
    client, out = make_client(tmp_path, "add_requirement", ADD)
    with ThreadPoolExecutor(max_workers=USERS) as pool:
        results = list(pool.map(lambda n: _save(client, n), range(USERS)))

    assert all(a == 200 and d == 200 and body["reply"] == "완료" for a, d, body in results), results

    keys = [r["req_key"] for r in client.get("/api/requirements").json()]
    assert len(keys) == len(set(keys)) == 8 + USERS  # 8 from PRDv1, one new number per user, none twice
    assert keys[8:] == [f"REQ-{n:03d}" for n in range(9, 9 + USERS)]

    conn = sqlite3.connect(tmp_path / "t.db")
    assert conn.execute("SELECT COUNT(*) FROM requirement_history WHERE source_type = 'manual'").fetchone()[0] == USERS
    versions = sorted(v for (v,) in conn.execute("SELECT version FROM documents WHERE kind = 'generated'"))
    conn.close()
    assert versions == list(range(2, 2 + USERS))  # every save made its own, uniquely numbered file
    assert sorted(p.name for p in out.iterdir()) == sorted(f"PRDv{v}.md" for v in versions)


def test_readers_are_not_blocked_by_writers(tmp_path):
    client, _ = make_client(tmp_path, "add_requirement", ADD)

    def read(_):
        return TestClient(client.app).get("/api/requirements").status_code

    with ThreadPoolExecutor(max_workers=USERS) as pool:
        writers = [pool.submit(_save, client, n) for n in range(4)]
        readers = list(pool.map(read, range(20)))
        [w.result() for w in writers]
    assert set(readers) == {200}
