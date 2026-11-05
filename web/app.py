"""Web front end (FastAPI). Thin: it passes messages to the agent graph and lists data.

Run (local, for people on the same network):
    python -m uvicorn --factory web.app:create_app --host 0.0.0.0 --port 8000

No login: requester / modifier are typed on the page and sent with the message. The agent
asks for confirmation through interrupt(); the page shows those questions as buttons and
sends the answers to /api/resume. Conversations live in the agent's checkpointer (SQLite), so
a reload or a restart keeps them.
"""
import re
import sqlite3
import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from config import config
from core.ingest.ingest import parse_document_name
from core.manual.new_version import DEFAULT_OUTPUT_DIR
from core.service import service
from infra.db.db import DEFAULT_DB_PATH
from interfaces.agent.agent import build_agent, pending_questions, resume, run_config, step, user_message

STATIC_DIR = Path(__file__).parent / "static"
MAX_UPLOAD_BYTES = 1024 * 1024  # 1 MB is far more than a requirements document needs
_SESSION_ID = r"^[A-Za-z0-9_-]{8,64}$"
_UPLOAD_NAME = re.compile(r"^[\w가-힣 ().\-]{1,100}\.md$")


class ChatRequest(BaseModel):
    session_id: str = Field(pattern=_SESSION_ID)
    message: str = Field(min_length=1, max_length=4000)


class ResumeRequest(BaseModel):
    session_id: str = Field(pattern=_SESSION_ID)
    answers: dict[str, Any]  # interrupt id -> bool (confirm) / option id (choice) / text (input)


def create_app(
    db_path=DEFAULT_DB_PATH,
    chroma_dir=config.CHROMA_DIR,
    upload_dir=Path("data") / "uploads",
    output_dir=DEFAULT_OUTPUT_DIR,
    checkpoint_path=Path("data") / "web_sessions.db",
    agent=None,
) -> FastAPI:
    db_path, output_dir, upload_dir = Path(db_path), Path(output_dir), Path(upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)

    if agent is None:
        from langgraph.checkpoint.sqlite import SqliteSaver

        checkpoint_path = Path(checkpoint_path)
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        saver = SqliteSaver(sqlite3.connect(checkpoint_path, check_same_thread=False))
        agent = build_agent(str(db_path), chroma_dir, output_dir=output_dir, checkpointer=saver,
                            allowed_input_dirs=[upload_dir])

    app = FastAPI(title="요구사항 이력 에이전트")
    session_locks: dict[str, threading.Lock] = {}
    registry_lock = threading.Lock()

    def lock_for(session_id: str) -> threading.Lock:
        with registry_lock:
            return session_locks.setdefault(session_id, threading.Lock())

    def config_for(session_id: str) -> dict:
        return run_config(session_id)  # memory key + LLM / tool call trace

    @app.exception_handler(ValueError)
    async def value_error(_, exc: ValueError):
        return JSONResponse({"error": str(exc)}, status_code=400)

    @app.exception_handler(Exception)
    async def unexpected(_, exc: Exception):  # never leak a stack trace to the page
        return JSONResponse({"error": f"처리 중 오류가 났습니다: {type(exc).__name__}"}, status_code=500)

    # ---- conversation ----------------------------------------------------------------

    @app.post("/api/chat")
    def chat(req: ChatRequest):
        cfg = config_for(req.session_id)
        with lock_for(req.session_id):  # one request per conversation at a time
            if pending_questions(agent, cfg):
                raise HTTPException(409, "먼저 확인 요청에 답해 주세요.")
            reply, pending = step(agent, user_message(req.message), cfg)
        return {"reply": reply, "pending": pending}

    @app.post("/api/resume")
    def resume_chat(req: ResumeRequest):
        cfg = config_for(req.session_id)
        with lock_for(req.session_id):
            waiting = {q["id"] for q in pending_questions(agent, cfg)}
            if not waiting:
                raise HTTPException(409, "답할 확인 요청이 없습니다.")
            if set(req.answers) != waiting:
                raise HTTPException(400, "모든 확인 요청에 답해야 합니다.")
            reply, pending = step(agent, resume(req.answers), cfg)
        return {"reply": reply, "pending": pending}

    @app.get("/api/session/{session_id}")
    def session(session_id: str):
        if not re.match(_SESSION_ID, session_id):
            raise HTTPException(422, "세션 ID 형식이 올바르지 않습니다.")
        cfg = config_for(session_id)
        state = agent.get_state(cfg)
        messages = []
        for m in (state.values or {}).get("messages", []):
            text = m.content if isinstance(m.content, str) else "".join(
                b.get("text", "") for b in m.content if isinstance(b, dict))
            if m.type in ("human", "ai") and text.strip():
                messages.append({"role": "user" if m.type == "human" else "agent", "text": text})
        return {"messages": messages, "pending": pending_questions(agent, cfg)}

    # ---- documents -----------------------------------------------------------------------

    @app.post("/api/upload")
    def upload(file: UploadFile = File(...)):
        name = Path(file.filename or "").name  # drop any folder part
        if not _UPLOAD_NAME.match(name):
            raise HTTPException(400, "Markdown(.md) 파일만 올릴 수 있고, 파일 이름에 쓸 수 없는 문자가 있습니다.")
        data = file.file.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(400, "파일이 너무 큽니다(최대 1MB).")
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            raise HTTPException(400, "UTF-8 텍스트 파일이 아닙니다.")
        target = (upload_dir / name).resolve()
        if upload_dir.resolve() not in target.parents:
            raise HTTPException(400, "올릴 수 없는 파일 이름입니다.")
        target.write_bytes(data)
        document, version = parse_document_name(name)
        return {"path": str(target), "name": name, "document": document, "version": version}

    @app.get("/api/documents")
    def documents():
        return service.list_documents(db_path)

    @app.get("/api/files/{document_id}")
    def download(document_id: int):
        """Only the new-version files the system generated can be downloaded."""
        for doc in service.list_documents(db_path):
            if doc["id"] == document_id and doc["kind"] == "generated":
                path = Path(doc["source_path"]).resolve()
                if output_dir.resolve() in path.parents and path.is_file():
                    return FileResponse(path, media_type="text/markdown", filename=path.name)
        raise HTTPException(404, "내려받을 수 있는 문서가 없습니다.")

    # ---- requirements and history ------------------------------------------------------

    @app.get("/api/requirements")
    def requirements():
        return service.list_requirements(db_path)

    @app.get("/api/requirements/{req_key}/history")
    def history(req_key: str):
        if not re.match(r"^REQ-\d+$", req_key, re.IGNORECASE):
            raise HTTPException(422, "요구사항 ID 형식이 올바르지 않습니다.")
        return service.requirement_history(db_path, req_key)

    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
