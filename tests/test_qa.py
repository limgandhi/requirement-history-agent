"""T7: question answering with sources from SQLite history."""
from pathlib import Path

import pytest

from infra.db.db import connect, init_schema
from infra.indexing.indexing import get_vectorstore, index_document
from core.ingest.ingest import ingest_document
from core.qa.qa import _LLMAnswer, ask

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    base = tmp_path_factory.mktemp("t7")
    conn = connect(base / "t7.db")
    init_schema(conn)
    r = ingest_document(conn, SAMPLES / "PRDv1.md", "lee", "kim")
    store = get_vectorstore(persist_dir=base / "chroma")
    index_document(conn, r.document_id, store)
    yield conn, store
    conn.close()


class _FakeLLM:
    def __init__(self, answer):
        self.answer, self.calls = answer, 0

    def with_structured_output(self, _):
        return self

    def invoke(self, _):
        self.calls += 1
        return self.answer


def test_llm_says_unanswerable_means_not_found(env):
    conn, store = env
    llm = _FakeLLM(_LLMAnswer(answerable=False, answer="찾지 못했다", used_req_keys=[]))
    ans = ask(conn, "투표 기간", store=store, llm=llm)
    assert llm.calls == 1
    assert not ans.found and ans.sources == []


def test_llm_cannot_invent_sources(env):
    conn, store = env
    llm = _FakeLLM(_LLMAnswer(answerable=True, answer="x", used_req_keys=["REQ-999"]))
    ans = ask(conn, "투표 기간은 최대 며칠인가?", store=store, llm=llm)
    assert not ans.found and ans.sources == []


def test_voting_period_answer_has_sources(env):
    conn, store = env
    ans = ask(conn, "투표 기간은 최대 며칠인가?", store=store)  # one real Bedrock call
    assert ans.found
    assert "7일" in ans.text
    src = [s for s in ans.sources if s.req_key == "REQ-003"]
    assert src
    assert (src[0].document_name, src[0].version) == ("PRD", 1)
    assert (src[0].requester, src[0].modifier) == ("lee", "kim")
