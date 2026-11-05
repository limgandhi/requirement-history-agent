"""Shared helpers for the interrupt tests: scripted model + fake Chroma store (no Bedrock)."""
from pathlib import Path

from langchain_core.documents import Document
from langchain_core.messages import AIMessage, ToolMessage

from core.ingest.ingest import ingest_document
from infra.db.db import connect, init_schema
from interfaces.agent.agent import build_agent, run_turn

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


class ScriptedModel:
    """Chat model: asks for one tool call, then says done. Also the QA LLM."""

    def __init__(self, tool_name, args, qa_answer=None):
        self.call = {"name": tool_name, "args": args, "id": "t1"}
        self.qa_answer = qa_answer
        self.qa_calls = 0
        self.turns = 0

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        if isinstance(messages[-1], ToolMessage):
            return AIMessage(content="완료")
        return AIMessage(content="", tool_calls=[self.call])

    def with_structured_output(self, _):
        outer = self

        class _Qa:
            def invoke(self, _prompt):
                outer.qa_calls += 1
                return outer.qa_answer

        return _Qa()


class FakeStore:
    """Returns the REQ-003 chunk only for queries listed in `hits_for`."""

    def __init__(self, hits_for=()):
        self.hits_for = set(hits_for)
        self.added = []

    def similarity_search_with_score(self, query, k=4):
        if query not in self.hits_for:
            return []
        meta = {"chunk_id": 1, "document_id": 1, "document_name": "PRD", "version": 1,
                "heading": "REQ-003 투표 기간", "req_key": "REQ-003"}
        return [(Document(page_content="REQ-003 투표 기간\n최대 7일", metadata=meta), 0.1)]

    def add_texts(self, **kw):
        self.added.append(kw)

    def get(self, **kw):
        return {"ids": []}

    def delete(self, **kw):
        pass


def make_db(tmp_path):
    conn = connect(tmp_path / "t.db")
    init_schema(conn)
    ingest_document(conn, SAMPLES / "PRDv1.md", "lee", "kim")
    conn.close()


def run_agent(tmp_path, model, store, answers, output_dir=None):
    """Run one turn; `answers` are the resume values for each interrupt in order."""
    make_db(tmp_path)
    agent = build_agent(tmp_path / "t.db", tmp_path / "chroma", llm=model, store=store, output_dir=output_dir or tmp_path / "out")
    seen = []
    queue = iter(answers)

    def respond(payload):
        seen.append(payload)
        return next(queue)

    cfg = {"configurable": {"thread_id": "x"}}
    reply = run_turn(agent, "go", cfg, respond)
    tool_text = [m.content for m in agent.get_state(cfg).values["messages"] if isinstance(m, ToolMessage)]
    return reply, tool_text, seen


def status(tmp_path, key):
    conn = connect(tmp_path / "t.db")
    row = conn.execute("SELECT status, content FROM requirements WHERE req_key = ?", (key,)).fetchone()
    count = conn.execute("SELECT COUNT(*) FROM requirement_history").fetchone()[0]
    conn.close()
    return row, count
