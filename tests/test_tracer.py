"""LLM / tool call trace: JSONL records written by the callback (no Bedrock)."""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphInterrupt

from config import config
from infra.llm.tracer import TEXT_LIMIT, FileTracer, get_text, get_tracer
from interfaces.agent.agent import build_agent, run_config, run_turn
from interrupt_helpers import FakeStore, make_db


def read(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()]


# ---- the handler itself --------------------------------------------------------------

def test_llm_events_carry_model_input_output_usage_and_latency(tmp_path):
    t = FileTracer(tmp_path / "t.jsonl")
    t.on_chat_model_start({}, [[AIMessage(content="시스템 규칙"), AIMessage(content=[{"type": "text", "text": "질문"}])]],
                          run_id="r1", parent_run_id="p1", metadata={"thread_id": "th-1", "ls_model_name": "sonnet"})
    msg = AIMessage(content="답", tool_calls=[{"name": "ask_history", "args": {"question": "q"}, "id": "c1"}],
                    usage_metadata={"input_tokens": 10, "output_tokens": 3, "total_tokens": 13})
    t.on_llm_end(LLMResult(generations=[[ChatGeneration(message=msg)]]), run_id="r1", parent_run_id="p1")

    start, end = read(tmp_path / "t.jsonl")
    assert (start["event"], start["model"], start["thread_id"], start["parent_run_id"]) == ("llm_start", "sonnet", "th-1", "p1")
    assert start["input"] == ["ai: 시스템 규칙", "ai: 질문"]
    assert (end["event"], end["model"], end["output"]) == ("llm_end", "sonnet", "답")
    assert end["usage"]["total_tokens"] == 13 and end["latency_s"] >= 0
    assert end["tool_calls"][0]["name"] == "ask_history" and "ts" in end


def test_tool_events_and_errors(tmp_path):
    t = FileTracer(tmp_path / "t.jsonl")
    t.on_tool_start({"name": "add_requirement"}, "{'title': 'a'}", run_id="t1", metadata={"thread_id": "th-1"})
    t.on_tool_end("추가 완료: REQ-009", run_id="t1")
    t.on_chat_model_start({}, [[AIMessage(content="x")]], run_id="r2")
    t.on_llm_error(RuntimeError("boom"), run_id="r2")
    t.on_tool_start({"name": "delete_requirement"}, "{}", run_id="t2")
    t.on_tool_error(GraphInterrupt(), run_id="t2")  # interrupt(): waiting for the user
    t.on_tool_start({"name": "ingest_document"}, "{}", run_id="t3")
    t.on_tool_error(ValueError("bad"), run_id="t3")

    events = read(tmp_path / "t.jsonl")
    assert [e["event"] for e in events] == ["tool_start", "tool_end", "llm_start", "llm_error", "tool_start",
                                            "tool_interrupt", "tool_start", "tool_error"]
    assert events[0]["tool"] == "add_requirement" and events[1]["output"] == "추가 완료: REQ-009"
    assert events[3]["error"] == "RuntimeError: boom"
    assert "error" not in events[5] and events[7]["error"] == "ValueError: bad"


def test_long_text_is_cut(tmp_path):
    t = FileTracer(tmp_path / "t.jsonl")
    t.on_tool_start({"name": "x"}, "가" * (TEXT_LIMIT + 50), run_id="t1")
    rec = read(tmp_path / "t.jsonl")[0]
    assert len(rec["input"]) < TEXT_LIMIT + 30 and rec["input"].endswith("(+50자)")


def test_concurrent_writes_keep_every_line_whole(tmp_path):
    t = FileTracer(tmp_path / "t.jsonl")
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda n: [t.on_tool_start({"name": f"tool{n}"}, "한글" * 200, run_id=f"{n}-{i}") for i in range(50)],
                      range(8)))
    assert len(read(tmp_path / "t.jsonl")) == 400  # every line parses; none lost or mixed


def test_a_logging_failure_never_raises(tmp_path):
    blocked = tmp_path / "file"
    blocked.write_text("x")
    t = FileTracer(blocked / "sub" / "t.jsonl")  # the parent is a file: cannot be created
    t.on_tool_start({"name": "x"}, "{}", run_id="t1")
    t.on_tool_end("ok", run_id="t1")  # no exception


def test_get_text_handles_block_lists():
    assert get_text(AIMessage(content=[{"type": "text", "text": "가"}, {"type": "x"}, {"type": "text", "text": "나"}])) == "가나"


def test_tracing_can_be_switched_off():
    assert config.TRACE_ENABLED is False and get_tracer() is None  # tests/conftest.py sets TRACE_ENABLED=0
    assert run_config("abc")["callbacks"] == [] and run_config("abc")["configurable"] == {"thread_id": "abc"}


# ---- through the agent graph ------------------------------------------------------------

class ToolThenDone(GenericFakeChatModel):
    """A real LangChain chat model (so callbacks fire) that picks one tool, then answers."""

    def bind_tools(self, tools, **kw):
        return self


def _chat_model(tool, args):
    replies = [AIMessage(content="", tool_calls=[{"name": tool, "args": args, "id": "c1"}]), AIMessage(content="완료")]
    return ToolThenDone(messages=iter(replies))


def test_agent_run_is_traced_end_to_end(tmp_path):
    make_db(tmp_path)
    tracer = FileTracer(tmp_path / "trace.jsonl")
    agent = build_agent(tmp_path / "t.db", tmp_path / "chroma", llm=_chat_model(
        "delete_requirement", {"req_key": "REQ-006", "requester": "a", "modifier": "b"}),
        store=FakeStore(), output_dir=tmp_path / "out", checkpointer=InMemorySaver())
    cfg = {"configurable": {"thread_id": "th-42"}, "callbacks": [tracer]}
    assert run_turn(agent, "REQ-006 삭제", cfg, lambda q: True) == "완료"

    events = read(tmp_path / "trace.jsonl")
    kinds = [e["event"] for e in events]
    assert kinds.count("llm_start") == kinds.count("llm_end") == 2  # tool choice, then the final answer
    assert "tool_start" in kinds and "tool_interrupt" in kinds and "tool_end" in kinds  # paused, resumed, finished
    assert {e["thread_id"] for e in events if "thread_id" in e} == {"th-42"}
    tool_end = [e for e in events if e["event"] == "tool_end"][-1]
    assert "삭제 완료" in tool_end["output"]
    first_llm = [e for e in events if e["event"] == "llm_start"][0]
    assert any("REQ-006 삭제" in line for line in first_llm["input"])
