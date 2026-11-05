"""Tool-calling agent as a LangGraph graph. Business logic stays in core.service.

START -> agent (LLM with tools bound) -> tools (ToolNode) -> agent ... -> END
Conversation history lives in the checkpointer, keyed by thread_id.
"""
from typing import Annotated, TypedDict

from langchain_core.messages import SystemMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.types import Command

from infra.llm.llm import get_llm
from interfaces.tools.tools import build_tools

SYSTEM_PROMPT = """당신은 요구사항 이력 에이전트다. 반드시 제공된 tool로만 일한다.
- 요구사항·이력 질문은 ask_history로 답하고, 결과를 그대로 전달한다. 출처를 지어내지 않는다.
- 추가·수정·삭제·문서 입력에는 요청자(requester)와 수정자(modifier)가 모두 필요하다.
  사용자가 말하지 않았으면 추측하지 말고 먼저 물어본다.
- 삭제는 사용자가 분명히 요청했을 때만 한다.
- 입력·추가·수정·삭제는 tool이 저장 전에 사용자 확인을 받는다. 확인 질문을 따로 하지 말고 tool을 호출한다.
- tool이 '오류:'를 돌려주면 내용을 그대로 알리고 임의로 재시도하지 않는다.
- 한국어로 답한다."""


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]


def build_agent(db_path, chroma_dir, llm=None, store=None, output_dir=None, checkpointer=None):
    """Compile the graph. Call it with config={"configurable": {"thread_id": ...}}."""
    kwargs = {"output_dir": output_dir} if output_dir else {}
    tools = build_tools(db_path, chroma_dir, llm=llm, store=store, **kwargs)
    model = (llm or get_llm()).bind_tools(tools)

    def call_model(state: AgentState) -> dict:
        return {"messages": [model.invoke([SystemMessage(SYSTEM_PROMPT)] + state["messages"])]}

    graph = StateGraph(AgentState)
    graph.add_node("agent", call_model)
    graph.add_node("tools", ToolNode(tools))
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", tools_condition)  # tool_calls -> "tools", else END
    graph.add_edge("tools", "agent")
    return graph.compile(checkpointer=checkpointer or InMemorySaver())


def reply_text(message) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") for b in content if isinstance(b, dict))


def run_turn(agent, text: str, run_config: dict, respond) -> str:
    """Send one user message and return the final reply.

    Whenever the graph pauses with interrupt() (confirmation, choice, input),
    respond(payload) is called with the interrupt's payload and its return value
    resumes the graph. Any front end can reuse this by supplying its own respond.
    """
    payload = {"messages": [("user", text)]}
    while True:
        result = agent.invoke(payload, run_config)
        pending = result.get("__interrupt__")
        if not pending:
            return reply_text(result["messages"][-1])
        # several tool calls may pause at once; answer each by its interrupt id
        payload = Command(resume={i.id: respond(i.value) for i in pending})
