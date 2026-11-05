"""T23-T25: detect changes between document versions and record them (PRDv1 -> PRDv2)."""
import shutil
from pathlib import Path

import pytest

from core.change_detection.changes import ingest_new_version, is_new_version, plan_new_version
from core.change_detection.detection import Item, detect_changes
from core.ingest.ingest import ingest_document
from core.manual.manual import add_requirement, delete_requirement, update_requirement
from infra.db.db import connect, init_schema
from infra.indexing.indexing import get_vectorstore, index_document, search_chunks

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


# ---- T23: the pure comparison -------------------------------------------------

ACTIVE = {"REQ-001": ("로그인", "이메일로 로그인한다."), "REQ-002": ("조직", "조직을 만든다."),
          "REQ-003": ("투표", "기간은 최대 7일이다.")}


def test_detect_added_changed_deleted_unchanged():
    items = [Item("REQ-001", "로그인", "이메일로   로그인한다.\n"),  # whitespace only: not a change
             Item("REQ-003", "투표", "기간은 최대 14일이다."),
             Item("REQ-004", "알림", "알림을 보낸다.")]
    d = detect_changes(ACTIVE, set(ACTIVE), items)
    assert d.unchanged == ["REQ-001"]
    assert [c.req_key for c in d.changed] == ["REQ-003"] and d.changed[0].old_content == "기간은 최대 7일이다."
    assert [c.req_key for c in d.added] == ["REQ-004"] and d.renumbered == []
    assert [c.req_key for c in d.deleted] == ["REQ-002"]


def test_title_change_counts_as_change():
    d = detect_changes(ACTIVE, set(ACTIVE), [Item("REQ-001", "로그인 방식", "이메일로 로그인한다.")])
    assert [c.req_key for c in d.changed] == ["REQ-001"]


def test_unknown_id_is_kept_but_deleted_id_and_missing_id_get_new_numbers():
    known = set(ACTIVE) | {"REQ-004"}  # REQ-004 existed once and was deleted
    items = [Item("REQ-004", "되살림", "삭제됐던 ID"), Item("REQ-012", "새것", "한 번도 없던 ID"), Item(None, "ID없음", "x")]
    d = detect_changes({}, known, items)
    assert [c.req_key for c in d.added] == ["REQ-005", "REQ-012", "REQ-006"]
    assert d.renumbered == [("REQ-004", "REQ-005")]


def test_new_number_never_takes_an_id_written_in_the_document():
    # the deleted REQ-001 would get the next number 4, but the document itself uses REQ-004
    d = detect_changes({}, {"REQ-001", "REQ-002", "REQ-003"}, [Item("REQ-001", "a", "b"), Item("REQ-004", "c", "d")])
    assert [c.req_key for c in d.added] == ["REQ-005", "REQ-004"]


def test_id_less_document_adds_everything_and_deletes_nothing():
    d = detect_changes(ACTIVE, set(ACTIVE), [Item(None, "a", "b"), Item(None, "c", "d")], id_less=True)
    assert [c.req_key for c in d.added] == ["REQ-004", "REQ-005"]
    assert d.deleted == [] and d.changed == [] and d.id_less


# ---- fixtures -------------------------------------------------------------------

class MemStore:
    """In-memory stand-in for Chroma: add_texts / get(where) / delete(ids)."""

    def __init__(self):
        self.items = {}

    def add_texts(self, texts, metadatas, ids):
        for t, m, i in zip(texts, metadatas, ids):
            self.items[i] = (t, m)

    def get(self, where=None):
        return {"ids": [i for i, (_, m) in self.items.items()
                        if all(m.get(k) == v for k, v in (where or {}).items())]}

    def delete(self, ids=None, where=None):
        for i in ids or []:
            self.items.pop(i, None)

    def keys(self):
        return sorted(m["req_key"] for _, m in self.items.values() if "req_key" in m)


@pytest.fixture
def env(tmp_path):
    conn = connect(tmp_path / "t.db")
    init_schema(conn)
    r1 = ingest_document(conn, SAMPLES / "PRDv1.md", "lee", "kim")
    store = MemStore()
    index_document(conn, r1.document_id, store)
    yield conn, store, tmp_path
    conn.close()


def _count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _history(conn, action=None):
    sql = ("SELECT h.*, r.req_key FROM requirement_history h JOIN requirements r ON r.id = h.requirement_id "
           "WHERE h.source_type = 'auto'")
    args = []
    if action:
        sql += " AND h.action = ?"
        args.append(action)
    return conn.execute(sql + " ORDER BY h.id", args).fetchall()


# ---- T24: save + history ----------------------------------------------------------

def test_prdv2_expected_result(env):
    conn, store, _ = env
    assert is_new_version(conn, SAMPLES / "PRDv2.md")
    d = plan_new_version(conn, SAMPLES / "PRDv2.md").detection
    assert [c.req_key for c in d.changed] == ["REQ-003"]
    assert [c.req_key for c in d.deleted] == ["REQ-006"]
    assert [c.req_key for c in d.added] == ["REQ-009", "REQ-010"] and d.renumbered == []
    assert len(d.unchanged) == 6


def test_prdv2_is_recorded_as_four_auto_history_rows(env):
    conn, store, _ = env
    before = _count(conn, "requirement_history")
    r = ingest_new_version(conn, SAMPLES / "PRDv2.md", "park", "choi", store=store)

    rows = _history(conn)[8:]  # the 8 rows before are v1's 'add' rows
    assert _count(conn, "requirement_history") == before + 4
    assert [(h["req_key"], h["action"]) for h in rows] == [
        ("REQ-009", "add"), ("REQ-010", "add"), ("REQ-003", "update"), ("REQ-006", "delete")]
    assert all((h["requester"], h["modifier"], h["document_id"]) == ("park", "choi", r.document_id) for h in rows)
    upd = [h for h in rows if h["action"] == "update"][0]
    assert "7일" in upd["old_value"] and "14일" in upd["new_value"]

    doc = conn.execute("SELECT name, version, kind FROM documents WHERE id = ?", (r.document_id,)).fetchone()
    assert tuple(doc) == ("PRD", 2, "input")
    assert conn.execute("SELECT COUNT(*) FROM chunks WHERE document_id = ?", (r.document_id,)).fetchone()[0] == r.chunk_count
    current = {x["req_key"]: x for x in conn.execute("SELECT * FROM requirements")}
    assert "14일" in current["REQ-003"]["content"] and current["REQ-006"]["status"] == "deleted"
    assert current["REQ-009"]["status"] == "active" and "익명" in current["REQ-009"]["title"]
    assert r.summary().startswith("PRDv2 변경 감지: 추가 2 / 변경 1 / 삭제 1 / 그대로 6")


def test_unchanged_requirements_get_no_history(env):
    conn, store, _ = env
    ingest_new_version(conn, SAMPLES / "PRDv2.md", "park", "choi", store=store)
    touched = {h["req_key"] for h in _history(conn)[8:]}
    assert not touched & {"REQ-001", "REQ-002", "REQ-004", "REQ-005", "REQ-007", "REQ-008"}


@pytest.mark.parametrize("requester,modifier", [("", "choi"), ("park", ""), ("  ", "  "), (None, "choi")])
def test_empty_actors_store_nothing(env, requester, modifier):
    conn, store, _ = env
    snapshot = (_count(conn, "documents"), _count(conn, "requirement_history"), list(store.items))
    with pytest.raises(ValueError, match="필수"):
        ingest_new_version(conn, SAMPLES / "PRDv2.md", requester, modifier, store=store)
    assert snapshot == (_count(conn, "documents"), _count(conn, "requirement_history"), list(store.items))


def test_per_requirement_actors_in_the_document_win(env):
    conn, store, base = env
    doc = base / "PRDv2.md"
    text = (SAMPLES / "PRDv1.md").read_text(encoding="utf-8").replace("최대 7일로 제한한다.", "최대 10일로 제한한다.")
    text = text.replace("최대 10일로 제한한다.", "최대 10일로 제한한다.\n\n- **요청자:** han\n- **수정자:** seo\n- **수정일:** 2026-11-05")
    doc.write_text(text, encoding="utf-8")
    ingest_new_version(conn, doc, "park", "choi", store=store)
    h = _history(conn, "update")
    assert len(h) == 1 and (h[0]["requester"], h[0]["modifier"]) == ("han", "seo")
    assert "10일" in conn.execute("SELECT content FROM requirements WHERE req_key = 'REQ-003'").fetchone()[0]


def test_no_changes_saves_nothing(env):
    conn, store, base = env
    same = base / "PRDv2.md"
    shutil.copy(SAMPLES / "PRDv1.md", same)
    docs = _count(conn, "documents")
    with pytest.raises(ValueError, match="바뀐 요구사항이 없어"):
        ingest_new_version(conn, same, "park", "choi", store=store)
    assert _count(conn, "documents") == docs


def test_version_rules(env):
    conn, store, base = env
    assert not is_new_version(conn, SAMPLES / "PRDv1.md")
    with pytest.raises(ValueError, match="이미 입력된 문서"):
        ingest_new_version(conn, SAMPLES / "PRDv1.md", "a", "b", store=store)
    ingest_new_version(conn, SAMPLES / "PRDv2.md", "a", "b", store=store)
    v1 = base / "PRDv1.md"
    shutil.copy(SAMPLES / "PRDv1.md", v1)
    with pytest.raises(ValueError, match="새 버전으로 입력할 수 없습니다"):  # v2 already exists
        ingest_new_version(conn, v1, "a", "b", store=store)


def test_compares_with_the_current_state_including_manual_changes(env):
    conn, store, _ = env
    update_requirement(conn, "REQ-003", "관리자는 투표 안건, 선택지, 투표 기간을 입력해 투표를 만들 수 있어야 한다. 투표 기간은 최대 14일로 제한한다.", "han", "seo")  # same as PRDv2
    delete_requirement(conn, "REQ-001", "han", "seo")  # PRDv2 still has REQ-001
    add_requirement(conn, "수동 추가", "손으로 넣은 요구사항", "han", "seo")  # becomes REQ-009, like PRDv2's own REQ-009
    d = plan_new_version(conn, SAMPLES / "PRDv2.md").detection
    assert "REQ-003" in d.unchanged  # already 14일 in the DB: not reported again
    assert [c.req_key for c in d.changed] == ["REQ-009"]  # same ID as the hand-added one: reported as a change
    assert [c.req_key for c in d.deleted] == ["REQ-006"]
    # REQ-001 was deleted, so its ID is retired and the document's REQ-001 is added under a new number
    assert d.renumbered == [("REQ-001", "REQ-011")]
    assert [c.req_key for c in d.added] == ["REQ-011", "REQ-010"]


# ---- T25: vectors -------------------------------------------------------------------

def test_vectors_follow_the_changes(env):
    conn, store, _ = env
    assert "REQ-006" in store.keys() and len(store.items) == 13
    ingest_new_version(conn, SAMPLES / "PRDv2.md", "park", "choi", store=store)
    keys = store.keys()
    assert "REQ-006" not in keys  # deleted: no vector left
    assert keys.count("REQ-003") == 1  # replaced, not duplicated
    text = {m["req_key"]: t for t, m in store.items.values() if "req_key" in m}
    assert "14일" in text["REQ-003"] and "7일" not in text["REQ-003"]
    assert "REQ-009" in keys and "REQ-010" in keys
    assert any(m.get("req_key") == "REQ-001" and i.startswith("chunk-") for i, (_, m) in store.items.items())  # untouched
    assert store.items[f"req-{conn.execute('SELECT id FROM requirements WHERE req_key = ?', ('REQ-003',)).fetchone()[0]}"][1]["version"] == 2


def test_chroma_failure_rolls_sqlite_back(env):
    conn, store, _ = env

    class Boom(MemStore):
        def add_texts(self, *a, **k):
            raise RuntimeError("chroma down")

    def snapshot():
        return (_count(conn, "documents"), _count(conn, "requirement_history"),
                conn.execute("SELECT content FROM requirements WHERE req_key = 'REQ-003'").fetchone()[0],
                conn.execute("SELECT status FROM requirements WHERE req_key = 'REQ-006'").fetchone()[0])

    before = snapshot()
    with pytest.raises(RuntimeError, match="chroma down"):
        ingest_new_version(conn, SAMPLES / "PRDv2.md", "park", "choi", store=Boom())
    assert snapshot() == before


def test_real_chroma_search_after_prdv2(tmp_path):
    """T25 completion check with the real Titan embeddings."""
    conn = connect(tmp_path / "t.db")
    init_schema(conn)
    r1 = ingest_document(conn, SAMPLES / "PRDv1.md", "lee", "kim")
    store = get_vectorstore(persist_dir=tmp_path / "chroma")
    index_document(conn, r1.document_id, store)
    ingest_new_version(conn, SAMPLES / "PRDv2.md", "park", "choi", store=store)

    top = search_chunks("투표 기간", k=1, store=store)[0]
    assert top.req_key == "REQ-003" and "14일" in top.content and "7일" not in top.content
    assert "REQ-006" not in [h.req_key for h in search_chunks("투표 마감 24시간 전 이메일 알림", k=8, store=store)]
    assert search_chunks("익명 투표", k=1, store=store)[0].req_key == "REQ-009"
    assert search_chunks("카카오 구글 소셜 로그인", k=1, store=store)[0].req_key == "REQ-010"
    conn.close()
