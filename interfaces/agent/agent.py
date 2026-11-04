"""Tool-calling agent. Picks tools from natural language; business logic stays in app.service."""
from langchain.agents import create_agent

from infra.llm.llm import get_llm
from interfaces.tools.tools import build_tools

SYSTEM_PROMPT = """당신은 요구사항 이력 에이전트다. 반드시 제공된 tool로만 일한다.
- 요구사항·이력 질문은 ask_history로 답하고, 결과를 그대로 전달한다. 출처를 지어내지 않는다.
- 추가·수정·삭제·문서 입력에는 요청자(requester)와 수정자(modifier)가 모두 필요하다.
  사용자가 말하지 않았으면 추측하지 말고 먼저 물어본다.
- 삭제는 사용자가 분명히 요청했을 때만 한다.
- tool이 '오류:'를 돌려주면 내용을 그대로 알리고 임의로 재시도하지 않는다.
- 한국어로 답한다."""


def build_agent(db_path, chroma_dir, llm=None, store=None, output_dir=None):
    kwargs = {"output_dir": output_dir} if output_dir else {}
    return create_agent(
        llm or get_llm(),
        build_tools(db_path, chroma_dir, llm=llm, store=store, **kwargs),
        system_prompt=SYSTEM_PROMPT,
    )


def reply_text(message) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") for b in content if isinstance(b, dict))
