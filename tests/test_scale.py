"""T18: a 100+ requirement document goes through ingest, storage, indexing and QA without loss.

Real Bedrock: embeddings for every chunk (~109) plus 1 QA call and 1 query embedding per question.
"""
import re
from pathlib import Path

import pytest

from core.ingest.ingest import ingest_document
from core.qa.qa import ask
from infra.db.db import connect, init_schema
from infra.indexing.indexing import get_vectorstore, index_document, search_chunks

BIG = Path(__file__).resolve().parent.parent / "samples" / "LargePRDv1.md"
REQUESTER, MODIFIER = "lee", "kim"

# (question, expected requirement, word the answer must contain): early, middle and late in the file
QUESTIONS = [
    ("조직 하나의 최대 구성원 수는 몇 명인가?", "REQ-012", "2,000"),
    ("같은 사용자는 1분에 API를 최대 몇 회 호출할 수 있나?", "REQ-078", "100"),
    ("API 키는 발급 후 며칠 동안 유효한가?", "REQ-100", "180"),
]


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    base = tmp_path_factory.mktemp("t18")
    conn = connect(base / "t18.db")
    init_schema(conn)
    result = ingest_document(conn, BIG, REQUESTER, MODIFIER)
    store = get_vectorstore(persist_dir=base / "chroma")
    indexed = index_document(conn, result.document_id, store)
    yield conn, store, result, indexed
    conn.close()


def _expected_keys():
    return re.findall(r"^### (REQ-\d+)", BIG.read_text(encoding="utf-8"), re.M)


def test_document_has_100_or_more_requirements():
    assert len(_expected_keys()) >= 100


def test_every_requirement_is_extracted_and_stored(env):
    conn, _, result, _ = env
    expected = _expected_keys()
    assert result.requirement_count == len(expected)
    stored = [r["req_key"] for r in conn.execute("SELECT req_key FROM requirements ORDER BY id")]
    assert stored == expected  # none lost, none duplicated, same order
    histories = conn.execute(
        "SELECT COUNT(*) FROM requirement_history WHERE action = 'add' AND source_type = 'auto' "
        "AND requester = ? AND modifier = ?", (REQUESTER, MODIFIER)).fetchone()[0]
    assert histories == len(expected)


def test_every_chunk_is_indexed(env):
    _, store, result, indexed = env
    assert indexed == result.chunk_count == store._collection.count()


@pytest.mark.parametrize("question,req,word", QUESTIONS, ids=[q[1] for q in QUESTIONS])
def test_question_answered_with_right_source(env, question, req, word):
    conn, store, _, _ = env
    assert req in [h.req_key for h in search_chunks(question, k=4, store=store)]
    answer = ask(conn, question, store=store)
    assert answer.found and word in answer.text, answer.text
    assert any((s.req_key, s.document_name, s.version, s.requester, s.modifier)
               == (req, "LargePRD", 1, REQUESTER, MODIFIER) for s in answer.sources), answer.sources
