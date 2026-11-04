"""T5: ingest a document, store requirements + history, enforce requester/modifier."""
from pathlib import Path

import pytest

from infra.db.db import TABLES, connect, init_schema
from core.extraction.extraction import _ExtractedList
from core.ingest.ingest import ingest_document, parse_document_name

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "test.db")
    init_schema(c)
    yield c
    c.close()


def _count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _total_rows(conn):
    return sum(_count(conn, t) for t in TABLES)


def test_ingest_prdv1_stores_requirements_and_history(conn):
    result = ingest_document(conn, SAMPLES / "PRDv1.md", "lee", "kim")

    assert (result.name, result.version) == ("PRD", 1)
    assert result.requirement_count == 8
    assert _count(conn, "requirements") == 8
    assert _count(conn, "requirement_history") == 8
    assert _count(conn, "documents") == 1
    assert _count(conn, "chunks") == result.chunk_count

    rows = conn.execute("SELECT * FROM requirement_history").fetchall()
    assert {r["action"] for r in rows} == {"add"}
    assert {r["source_type"] for r in rows} == {"auto"}
    assert {(r["requester"], r["modifier"]) for r in rows} == {("lee", "kim")}


def test_stored_requirement_matches_document(conn):
    ingest_document(conn, SAMPLES / "PRDv1.md", "lee", "kim")
    row = conn.execute("SELECT * FROM requirements WHERE req_key='REQ-003'").fetchone()
    assert row["title"] == "정기 투표 생성"
    assert "7일" in row["content"]
    assert row["status"] == "active"


@pytest.mark.parametrize("requester,modifier", [("", "kim"), ("lee", ""), ("  ", "kim"), (None, "kim")])
def test_missing_actor_is_rejected_and_nothing_is_saved(conn, requester, modifier):
    with pytest.raises(ValueError, match="필수"):
        ingest_document(conn, SAMPLES / "PRDv1.md", requester, modifier)
    assert _total_rows(conn) == 0


def test_actor_check_runs_before_reading_the_file(conn, tmp_path):
    with pytest.raises(ValueError, match="필수"):
        ingest_document(conn, tmp_path / "does_not_exist.md", "", "")


def test_same_document_twice_is_rejected(conn):
    ingest_document(conn, SAMPLES / "PRDv1.md", "lee", "kim")
    before = _total_rows(conn)
    with pytest.raises(ValueError, match="이미 입력"):
        ingest_document(conn, SAMPLES / "PRDv1.md", "lee", "kim")
    assert _total_rows(conn) == before


def test_new_version_with_existing_ids_is_rejected_without_partial_save(conn):
    ingest_document(conn, SAMPLES / "PRDv1.md", "lee", "kim")
    before = _total_rows(conn)
    with pytest.raises(ValueError, match="이미 저장된 요구사항 ID"):
        ingest_document(conn, SAMPLES / "PRDv2.md", "lee", "kim")
    assert _total_rows(conn) == before


def test_document_without_requirements_is_rejected(conn, tmp_path):
    class EmptyLLM:
        def with_structured_output(self, schema):
            return self

        def invoke(self, prompt):
            return _ExtractedList(requirements=[])

    doc = tmp_path / "notes.md"
    doc.write_text("## 메모\n\n그냥 메모입니다.\n", encoding="utf-8")
    with pytest.raises(ValueError, match="찾지 못"):
        ingest_document(conn, doc, "lee", "kim", llm=EmptyLLM())
    assert _total_rows(conn) == 0


@pytest.mark.parametrize(
    "filename,expected",
    [("PRDv1.md", ("PRD", 1)), ("PRDv12.md", ("PRD", 12)), ("PRD_v2.md", ("PRD", 2)), ("spec.md", ("spec", 1))],
)
def test_parse_document_name(filename, expected):
    assert parse_document_name(filename) == expected


# ---- T8: ingest -> ask through the tools on an empty DB ----
def _tools(tmp_path, name="c.db"):
    from interfaces.tools.tools import build_tools

    return {t.name: t for t in build_tools(tmp_path / name, tmp_path / "chroma")}


def test_tool_ingest_then_ask(tmp_path):
    t = _tools(tmp_path)
    out = t["ingest_document"].invoke(
        {"path": str(SAMPLES / "PRDv1.md"), "requester": "lee", "modifier": "kim"})
    assert out.startswith("입력 완료")

    out = t["ask_history"].invoke({"question": "투표 기간은 최대 며칠인가?"})  # one real Bedrock call
    assert "7일" in out
    for expected in ("PRDv1", "REQ-003", "lee", "kim"):
        assert expected in out


def test_tool_ingest_without_actors_fails_and_saves_nothing(tmp_path):
    out = _tools(tmp_path)["ingest_document"].invoke(
        {"path": str(SAMPLES / "PRDv1.md"), "requester": "", "modifier": ""})
    assert out.startswith("오류") and "필수" in out
    assert not (tmp_path / "c.db").exists()  # validation runs before the DB is even created


def test_tool_ask_without_db_reports_error(tmp_path):
    out = _tools(tmp_path, "none.db")["ask_history"].invoke({"question": "q"})
    assert out.startswith("오류") and "ingest" in out


def test_chat_loop_passes_input_to_agent_and_stops_on_exit(monkeypatch, capsys):
    from langchain_core.messages import AIMessage
    from main import run_chat

    class FakeAgent:
        def invoke(self, state, config):
            assert config["configurable"]["thread_id"]
            return {"messages": [AIMessage(content="응답")]}

    inputs = iter(["안녕", "exit"])
    monkeypatch.setattr("builtins.input", lambda _="": next(inputs))
    run_chat(agent=FakeAgent())
    assert "응답" in capsys.readouterr().out
