"""T9: manual add, T10: manual update. T11: manual delete."""
from pathlib import Path

import pytest

from infra.db.db import TABLES, connect, init_schema
from core.ingest.ingest import ingest_document
from infra.indexing.indexing import get_vectorstore, index_document, search_chunks
from core.manual.manual import add_requirement, delete_requirement, update_requirement
from core.qa.qa import ask

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "test.db")
    init_schema(c)
    yield c
    c.close()


def _total_rows(conn):
    return sum(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES)


def test_manual_add_stores_requirement_and_manual_history(conn):
    key = add_requirement(conn, "알림 설정", "사용자는 알림을 끌 수 있다.", "lee", "kim", req_key="REQ-020")
    assert key == "REQ-020"

    req = conn.execute("SELECT * FROM requirements WHERE req_key = 'REQ-020'").fetchone()
    assert (req["title"], req["status"]) == ("알림 설정", "active")

    hist = conn.execute("SELECT * FROM requirement_history WHERE requirement_id = ?", (req["id"],)).fetchall()
    assert len(hist) == 1
    assert (hist[0]["action"], hist[0]["source_type"]) == ("add", "manual")
    assert (hist[0]["requester"], hist[0]["modifier"]) == ("lee", "kim")
    assert hist[0]["new_value"] == "사용자는 알림을 끌 수 있다."


def test_manual_add_assigns_next_id_after_ingest(conn):
    ingest_document(conn, SAMPLES / "PRDv1.md", "lee", "kim")
    assert add_requirement(conn, "새 기능", "내용", "lee", "kim") == "REQ-009"
    assert add_requirement(conn, "또 다른 기능", "내용", "lee", "kim") == "REQ-010"


@pytest.mark.parametrize("requester,modifier", [("", "kim"), ("lee", ""), ("  ", "kim"), (None, "kim"), ("lee", None)])
def test_manual_add_without_actor_saves_nothing(conn, requester, modifier):
    with pytest.raises(ValueError, match="필수"):
        add_requirement(conn, "제목", "내용", requester, modifier)
    assert _total_rows(conn) == 0


@pytest.mark.parametrize("title,content", [("", "내용"), ("제목", "  ")])
def test_manual_add_requires_title_and_content(conn, title, content):
    with pytest.raises(ValueError, match="필수"):
        add_requirement(conn, title, content, "lee", "kim")
    assert _total_rows(conn) == 0


def test_manual_add_rejects_duplicate_or_bad_id(conn):
    add_requirement(conn, "a", "b", "lee", "kim", req_key="REQ-001")
    before = _total_rows(conn)
    with pytest.raises(ValueError, match="이미 존재"):
        add_requirement(conn, "a", "b", "lee", "kim", req_key="req-001")
    with pytest.raises(ValueError, match="형식"):
        add_requirement(conn, "a", "b", "lee", "kim", req_key="FOO-1")
    assert _total_rows(conn) == before


# ---- T10: manual update ----
@pytest.fixture
def ingested(conn):
    ingest_document(conn, SAMPLES / "PRDv1.md", "lee", "kim")
    return conn


def test_manual_update_keeps_old_and_new_value_in_history(ingested):
    old = ingested.execute("SELECT content FROM requirements WHERE req_key='REQ-003'").fetchone()["content"]
    update_requirement(ingested, "req-003", "투표 기간은 최대 14일이다.", "park", "choi")

    req = ingested.execute("SELECT * FROM requirements WHERE req_key='REQ-003'").fetchone()
    assert req["content"] == "투표 기간은 최대 14일이다."
    hist = ingested.execute(
        "SELECT * FROM requirement_history WHERE requirement_id=? ORDER BY id", (req["id"],)
    ).fetchall()
    assert [h["action"] for h in hist] == ["add", "update"]
    assert (hist[1]["source_type"], hist[1]["requester"], hist[1]["modifier"]) == ("manual", "park", "choi")
    assert hist[1]["old_value"] == old
    assert hist[1]["new_value"] == "투표 기간은 최대 14일이다."


@pytest.mark.parametrize("requester,modifier", [("", "kim"), ("lee", ""), (None, None)])
def test_manual_update_without_actor_changes_nothing(ingested, requester, modifier):
    before = _total_rows(ingested)
    old = ingested.execute("SELECT content FROM requirements WHERE req_key='REQ-003'").fetchone()["content"]
    with pytest.raises(ValueError, match="필수"):
        update_requirement(ingested, "REQ-003", "바뀐 내용", requester, modifier)
    assert _total_rows(ingested) == before
    assert ingested.execute("SELECT content FROM requirements WHERE req_key='REQ-003'").fetchone()["content"] == old


def test_manual_update_rejects_unknown_empty_or_same_content(ingested):
    before = _total_rows(ingested)
    same = ingested.execute("SELECT content FROM requirements WHERE req_key='REQ-003'").fetchone()["content"]
    with pytest.raises(ValueError, match="찾을 수 없"):
        update_requirement(ingested, "REQ-999", "x", "lee", "kim")
    with pytest.raises(ValueError, match="필수"):
        update_requirement(ingested, "REQ-003", "  ", "lee", "kim")
    with pytest.raises(ValueError, match="같아"):
        update_requirement(ingested, "REQ-003", same, "lee", "kim")
    assert _total_rows(ingested) == before


def test_manual_update_rejects_deleted_requirement(ingested):
    ingested.execute("UPDATE requirements SET status='deleted' WHERE req_key='REQ-003'")
    with pytest.raises(ValueError, match="삭제된"):
        update_requirement(ingested, "REQ-003", "x", "lee", "kim")


def test_manual_update_rolls_back_when_chroma_fails(ingested):
    class BrokenStore:
        def add_texts(self, **kwargs):
            raise RuntimeError("chroma down")

    before = _total_rows(ingested)
    with pytest.raises(RuntimeError):
        update_requirement(ingested, "REQ-003", "바뀐 내용", "lee", "kim", store=BrokenStore())
    assert _total_rows(ingested) == before
    assert "바뀐 내용" not in ingested.execute(
        "SELECT content FROM requirements WHERE req_key='REQ-003'").fetchone()["content"]


def test_manual_update_refreshes_search_index(ingested, tmp_path):
    """Uses real Titan embeddings (a handful of calls)."""
    doc_id = ingested.execute("SELECT id FROM documents").fetchone()["id"]
    store = get_vectorstore(persist_dir=tmp_path / "chroma")
    index_document(ingested, doc_id, store)
    total = store._collection.count()

    update_requirement(
        ingested, "REQ-003", "정기 투표는 최대 14일까지 진행할 수 있다. 연장 투표는 사전 공지가 필요하다.",
        "lee", "kim", store=store,
    )

    assert store._collection.count() == total  # old vector replaced, not added to
    assert len(store.get(where={"req_key": "REQ-003"})["ids"]) == 1
    hit = search_chunks("투표 기간 14일 연장", k=1, store=store)[0]
    assert hit.req_key == "REQ-003"
    assert "14일" in hit.content


# ---- T11: manual delete ----
class _FakeEmbeddings:
    """Deterministic embeddings so delete tests need no Bedrock call."""

    def _vec(self, text):
        return [float(len(text) % 7 + 1), float(sum(map(ord, text)) % 11 + 1), 1.0]

    def embed_documents(self, texts):
        return [self._vec(t) for t in texts]

    def embed_query(self, text):
        return self._vec(text)


def test_manual_delete_marks_deleted_and_keeps_history(ingested):
    old = ingested.execute("SELECT content FROM requirements WHERE req_key='REQ-006'").fetchone()["content"]
    delete_requirement(ingested, "req-006", "park", "choi")

    req = ingested.execute("SELECT * FROM requirements WHERE req_key='REQ-006'").fetchone()
    assert req["status"] == "deleted"
    hist = ingested.execute(
        "SELECT * FROM requirement_history WHERE requirement_id=? ORDER BY id", (req["id"],)
    ).fetchall()
    assert [h["action"] for h in hist] == ["add", "delete"]
    assert (hist[1]["source_type"], hist[1]["requester"], hist[1]["modifier"]) == ("manual", "park", "choi")
    assert hist[1]["old_value"] == old
    assert hist[1]["new_value"] is None


@pytest.mark.parametrize("requester,modifier", [("", "kim"), ("lee", ""), (None, None)])
def test_manual_delete_without_actor_changes_nothing(ingested, requester, modifier):
    before = _total_rows(ingested)
    with pytest.raises(ValueError, match="필수"):
        delete_requirement(ingested, "REQ-006", requester, modifier)
    assert _total_rows(ingested) == before
    assert ingested.execute("SELECT status FROM requirements WHERE req_key='REQ-006'").fetchone()["status"] == "active"


def test_manual_delete_rejects_unknown_or_already_deleted(ingested):
    delete_requirement(ingested, "REQ-006", "lee", "kim")
    before = _total_rows(ingested)
    with pytest.raises(ValueError, match="이미 삭제"):
        delete_requirement(ingested, "REQ-006", "lee", "kim")
    with pytest.raises(ValueError, match="찾을 수 없"):
        delete_requirement(ingested, "REQ-999", "lee", "kim")
    assert _total_rows(ingested) == before


def test_manual_delete_removes_chroma_vectors(ingested, tmp_path):
    doc_id = ingested.execute("SELECT id FROM documents").fetchone()["id"]
    store = get_vectorstore(persist_dir=tmp_path / "chroma", embeddings=_FakeEmbeddings())
    index_document(ingested, doc_id, store)
    total = store._collection.count()
    assert len(store.get(where={"req_key": "REQ-006"})["ids"]) == 1

    delete_requirement(ingested, "REQ-006", "lee", "kim", store=store)

    assert store.get(where={"req_key": "REQ-006"})["ids"] == []
    assert store._collection.count() == total - 1


def test_manual_delete_rolls_back_when_chroma_fails(ingested):
    class BrokenStore:
        def get(self, **kwargs):
            raise RuntimeError("chroma down")

    before = _total_rows(ingested)
    with pytest.raises(RuntimeError):
        delete_requirement(ingested, "REQ-006", "lee", "kim", store=BrokenStore())
    assert _total_rows(ingested) == before
    assert ingested.execute("SELECT status FROM requirements WHERE req_key='REQ-006'").fetchone()["status"] == "active"


def test_ask_never_presents_deleted_requirement_as_current(ingested, tmp_path):
    """Even a stale vector of a deleted requirement must not reach the LLM."""
    doc_id = ingested.execute("SELECT id FROM documents").fetchone()["id"]
    store = get_vectorstore(persist_dir=tmp_path / "chroma", embeddings=_FakeEmbeddings())
    index_document(ingested, doc_id, store)
    ingested.execute("UPDATE requirements SET status='deleted'")  # simulate stale index

    class ForbiddenLLM:
        def with_structured_output(self, schema):
            raise AssertionError("LLM must not be called without a current requirement")

    answer = ask(ingested, "투표 기간은 며칠인가?", store=store, llm=ForbiddenLLM())
    assert answer.found is False
    assert "찾지 못" in answer.text
