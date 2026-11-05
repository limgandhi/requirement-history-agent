"""Service entry point: `python main.py` starts the agent chat. No arguments.

The agent is a LangGraph graph (interfaces/agent); every feature is a tool it calls.
No business logic here. DB and Chroma paths come from config defaults;
credentials from .env (load_dotenv).
"""
import uuid

from config import config
from infra.db.db import DEFAULT_DB_PATH
from interfaces.agent.agent import build_agent, run_turn


_YES = ("y", "yes", "예", "네", "응", "ㅇ")


def cli_respond(payload: dict):
    """Console side of interrupt(): show the question, return the user's answer."""
    print(payload["message"])
    kind = payload["kind"]
    if kind == "confirm":
        return input("[y/N] ").strip().lower() in _YES
    if kind == "choice":
        options = payload["options"]
        for n, o in enumerate(options, 1):
            print(f"  {n}. {o['label']}")
        while True:
            picked = input("번호 또는 이름: ").strip()
            for n, o in enumerate(options, 1):
                if picked in (str(n), o["id"], o["label"]):
                    return o["id"]
            print("목록에 있는 번호를 입력하세요.")
    return input("> ").strip()


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
        print(run_turn(agent, text, run_config, cli_respond))


if __name__ == "__main__":
    run_chat()
