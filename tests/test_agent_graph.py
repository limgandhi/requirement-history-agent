"""LangGraph agent: tool routing and per-thread memory, with a stub model (no Bedrock)."""
from langchain_core.messages import AIMessage, ToolMessage

from src.interfaces.agent.agent import build_agent, reply_text


class StubModel:
    """First call asks for a tool, later calls answer in text."""

    def __init__(self):
        self.calls = 0
        self.seen = []

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        self.calls += 1
        self.seen.append(len(messages))
        if self.calls == 1:
            return AIMessage(content="", tool_calls=[
                {"name": "ask_history", "args": {"question": "q"}, "id": "t1"}])
        return AIMessage(content="완료")


def _agent(tmp_path, model):
    return build_agent(tmp_path / "none.db", tmp_path / "chroma", llm=model)


def test_graph_routes_through_tool_then_answers(tmp_path):
    agent = _agent(tmp_path, StubModel())
    cfg = {"configurable": {"thread_id": "a"}}
    out = agent.invoke({"messages": [("user", "질문")]}, cfg)["messages"]
    tool_msgs = [m for m in out if isinstance(m, ToolMessage)]
    assert len(tool_msgs) == 1 and tool_msgs[0].content.startswith("오류")  # DB missing -> error text
    assert reply_text(out[-1]) == "완료"


def test_threads_keep_separate_history(tmp_path):
    model = StubModel()
    agent = _agent(tmp_path, model)
    a = {"configurable": {"thread_id": "a"}}
    agent.invoke({"messages": [("user", "1")]}, a)
    n = len(agent.invoke({"messages": [("user", "2")]}, a)["messages"])
    m = len(agent.invoke({"messages": [("user", "x")]}, {"configurable": {"thread_id": "b"}})["messages"])
    assert n > m  # thread a remembers turn 1, thread b starts fresh
