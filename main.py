"""Service entry point: `python main.py` starts the agent chat. No arguments.

The agent is a LangGraph graph (interfaces/agent); every feature is a tool it calls.
No business logic here. DB and Chroma paths come from config defaults;
credentials from .env (load_dotenv).
"""
import uuid

from config import config
from infra.db.db import DEFAULT_DB_PATH
from interfaces.agent.agent import build_agent, reply_text


def run_chat(db_path=DEFAULT_DB_PATH, chroma_dir=config.CHROMA_DIR, agent=None) -> None:
    agent = agent or build_agent(str(db_path), chroma_dir)
    run_config = {"configurable": {"thread_id": str(uuid.uuid4())}}  # one conversation per run
    print("에이전트를 시작합니다. 종료하려면 빈 줄 또는 exit 을 입력하세요.")
    while True:
        try:
            text = input("> ").strip()
        except EOFError:
            break
        if not text or text.lower() in ("exit", "quit"):
            break
        result = agent.invoke({"messages": [("user", text)]}, run_config)
        print(reply_text(result["messages"][-1]))


if __name__ == "__main__":
    run_chat()
