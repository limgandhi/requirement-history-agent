"""T31-T34, T36: the web API with a scripted model and a fake Chroma store (no Bedrock)."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from src.interfaces.agent.agent import build_agent
from src.web.app import create_app
from interrupt_helpers import FakeStore, ScriptedModel, make_db

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
SID = "session-0001"


def make_client(tmp_path, tool=None, args=None, qa_answer=None, db=True):
    if db:
        make_db(tmp_path)  # PRDv1 stored as lee/kim
    uploads, out = tmp_path / "uploads", tmp_path / "out"
    uploads.mkdir(exist_ok=True)
    model = ScriptedModel(tool or "ask_history", args or {"question": "q"}, qa_answer=qa_answer)
    agent = build_agent(tmp_path / "t.db", tmp_path / "chroma", llm=model, store=FakeStore(), output_dir=out,
                        checkpointer=InMemorySaver(), allowed_input_dirs=[uploads])
    app = create_app(db_path=tmp_path / "t.db", chroma_dir=tmp_path / "chroma", upload_dir=uploads,
                     output_dir=out, agent=agent)
    return TestClient(app, raise_server_exceptions=False), out


DELETE = {"req_key": "REQ-006", "requester": "park", "modifier": "choi"}


def test_index_page_and_static_files(tmp_path):
    client, _ = make_client(tmp_path)
    page = client.get("/")
    assert page.status_code == 200 and "요구사항 이력 에이전트" in page.text
    assert client.get("/static/app.js").status_code == 200 and client.get("/static/style.css").status_code == 200


def test_chat_answers_without_confirmation(tmp_path):
    client, _ = make_client(tmp_path)  # ask_history on an empty search -> asks for an alternative
    r = client.post("/api/chat", json={"session_id": SID, "message": "없는 기능"})
    assert r.status_code == 200
    body = r.json()
    assert body["reply"] is None and body["pending"][0]["kind"] == "choice"
    assert [o["id"] for o in body["pending"][0]["options"]] == ["similar", "rephrase", "cancel"]
    done = client.post("/api/resume", json={"session_id": SID, "answers": {body["pending"][0]["id"]: "cancel"}})
    assert done.json()["reply"] == "완료" and done.json()["pending"] == []


def test_delete_waits_for_confirmation_then_saves_and_makes_a_new_version(tmp_path):
    client, out = make_client(tmp_path, "delete_requirement", DELETE)
    r = client.post("/api/chat", json={"session_id": SID, "message": "REQ-006 삭제"}).json()
    q = r["pending"][0]
    assert r["reply"] is None and q["kind"] == "confirm" and "REQ-006" in q["message"]
    assert next(x for x in client.get("/api/requirements").json() if x["req_key"] == "REQ-006")["status"] == "active"

    done = client.post("/api/resume", json={"session_id": SID, "answers": {q["id"]: True}}).json()
    assert done["reply"] == "완료" and done["pending"] == []
    assert next(x for x in client.get("/api/requirements").json() if x["req_key"] == "REQ-006")["status"] == "deleted"

    docs = client.get("/api/documents").json()
    generated = [d for d in docs if d["kind"] == "generated"]
    assert [(d["name"], d["version"]) for d in generated] == [("PRD", 2)]
    file = client.get(f"/api/files/{generated[0]['id']}")
    assert file.status_code == 200 and "REQ-006 투표 마감 알림 (삭제일" in file.text
    assert (out / "PRDv2.md").exists()


def test_declining_changes_nothing(tmp_path):
    client, out = make_client(tmp_path, "delete_requirement", DELETE)
    q = client.post("/api/chat", json={"session_id": SID, "message": "삭제"}).json()["pending"][0]
    client.post("/api/resume", json={"session_id": SID, "answers": {q["id"]: False}})
    assert next(x for x in client.get("/api/requirements").json() if x["req_key"] == "REQ-006")["status"] == "active"
    assert not out.exists() or list(out.iterdir()) == []


def test_chat_is_refused_while_a_question_is_waiting(tmp_path):
    client, _ = make_client(tmp_path, "delete_requirement", DELETE)
    client.post("/api/chat", json={"session_id": SID, "message": "삭제"})
    again = client.post("/api/chat", json={"session_id": SID, "message": "다른 요청"})
    assert again.status_code == 409 and "확인" in again.json()["detail"]


def test_resume_needs_a_waiting_question_and_all_answers(tmp_path):
    client, _ = make_client(tmp_path, "delete_requirement", DELETE)
    assert client.post("/api/resume", json={"session_id": SID, "answers": {"x": True}}).status_code == 409
    client.post("/api/chat", json={"session_id": SID, "message": "삭제"})
    assert client.post("/api/resume", json={"session_id": SID, "answers": {"wrong-id": True}}).status_code == 400


def test_session_restores_messages_and_pending_question(tmp_path):
    client, _ = make_client(tmp_path, "delete_requirement", DELETE)
    client.post("/api/chat", json={"session_id": SID, "message": "REQ-006 삭제해줘"})
    state = client.get(f"/api/session/{SID}").json()
    assert state["messages"][0] == {"role": "user", "text": "REQ-006 삭제해줘"}
    assert state["pending"][0]["kind"] == "confirm"
    assert client.get("/api/session/other-session-1").json() == {"messages": [], "pending": []}


@pytest.mark.parametrize("bad", ["short", "has space 12345", "../../etc/passwd", "x" * 65])
def test_session_id_is_validated(tmp_path, bad):
    client, _ = make_client(tmp_path)
    assert client.post("/api/chat", json={"session_id": bad, "message": "q"}).status_code == 422


def test_message_length_is_limited(tmp_path):
    client, _ = make_client(tmp_path)
    assert client.post("/api/chat", json={"session_id": SID, "message": ""}).status_code == 422
    assert client.post("/api/chat", json={"session_id": SID, "message": "가" * 4001}).status_code == 422


# ---- requirements, history -----------------------------------------------------------

def test_requirements_and_history_views(tmp_path):
    client, _ = make_client(tmp_path)
    reqs = client.get("/api/requirements").json()
    assert [r["req_key"] for r in reqs] == [f"REQ-00{i}" for i in range(1, 9)]
    h = client.get("/api/requirements/req-003/history").json()
    assert len(h) == 1
    assert (h[0]["action"], h[0]["source_type"], h[0]["requester"], h[0]["modifier"], h[0]["document"], h[0]["version"]) \
        == ("add", "auto", "lee", "kim", "PRD", 1)
    assert client.get("/api/requirements/REQ-999/history").json() == []
    assert client.get("/api/requirements/not-an-id/history").status_code == 422


def test_empty_database_gives_empty_lists(tmp_path):
    client, _ = make_client(tmp_path, db=False)
    assert client.get("/api/requirements").json() == [] and client.get("/api/documents").json() == []


# ---- upload -------------------------------------------------------------------------------

def _upload(client, name, data, content_type="text/markdown"):
    return client.post("/api/upload", files={"file": (name, data, content_type)})


def test_upload_saves_the_file_and_reads_name_and_version(tmp_path):
    client, _ = make_client(tmp_path)
    r = _upload(client, "PRDv2.md", (SAMPLES / "PRDv2.md").read_bytes())
    assert r.status_code == 200
    body = r.json()
    assert (body["document"], body["version"]) == ("PRD", 2)
    assert Path(body["path"]).read_bytes() == (SAMPLES / "PRDv2.md").read_bytes()
    assert Path(body["path"]).parent == (tmp_path / "uploads").resolve()


@pytest.mark.parametrize("name,data", [
    ("notes.txt", b"x"), ("PRDv2.md.exe", b"x"), ("..", b"x"), ("bad|name.md", b"x"), ("", b"x")])
def test_upload_rejects_wrong_names(tmp_path, name, data):
    client, _ = make_client(tmp_path)
    assert _upload(client, name, data).status_code in (400, 422)


def test_upload_drops_folder_parts_and_rejects_big_or_binary_files(tmp_path):
    client, _ = make_client(tmp_path)
    ok = _upload(client, "../../evil/PRDv3.md", "# 문서\n".encode("utf-8"))
    assert ok.status_code == 200 and Path(ok.json()["path"]).parent == (tmp_path / "uploads").resolve()
    assert _upload(client, "Big.md", b"a" * (1024 * 1024 + 1)).status_code == 400
    assert _upload(client, "Bin.md", b"\xff\xfe\x00\x01").status_code == 400


def test_uploaded_new_version_goes_through_change_detection_with_confirmation(tmp_path):
    client, _ = make_client(tmp_path)
    uploaded = _upload(client, "PRDv2.md", (SAMPLES / "PRDv2.md").read_bytes()).json()
    # a second app whose scripted model inputs the uploaded file
    args = {"path": uploaded["path"], "requester": "park", "modifier": "choi"}
    client2, _ = make_client(tmp_path, "ingest_document", args, db=False)
    q = client2.post("/api/chat", json={"session_id": SID, "message": "입력해줘"}).json()["pending"][0]
    assert "추가 2 / 변경 1 / 삭제 1 / 그대로 6" in q["message"]
    done = client2.post("/api/resume", json={"session_id": SID, "answers": {q["id"]: True}}).json()
    assert done["reply"] == "완료"
    assert next(x for x in client2.get("/api/requirements").json() if x["req_key"] == "REQ-009")["status"] == "active"
    history = client2.get("/api/requirements/REQ-003/history").json()
    assert [(h["action"], h["source_type"], h["document"], h["version"]) for h in history] == [
        ("add", "auto", "PRD", 1), ("update", "auto", "PRD", 2)]


def test_documents_outside_the_upload_folder_are_refused(tmp_path):
    args = {"path": str(SAMPLES / "PRDv2.md"), "requester": "park", "modifier": "choi"}
    client, _ = make_client(tmp_path, "ingest_document", args)
    client.post("/api/chat", json={"session_id": SID, "message": "입력"})  # tool answers with an error text
    texts = [m["text"] for m in client.get(f"/api/session/{SID}").json()["messages"]]
    assert texts[-1] == "완료"  # the model finished; the tool itself refused (no question was asked)
    assert client.get(f"/api/session/{SID}").json()["pending"] == []
    assert len(client.get("/api/documents").json()) == 1  # PRDv2 was not read from outside the upload folder
    assert next(x for x in client.get("/api/requirements").json() if x["req_key"] == "REQ-006")["status"] == "active"


def test_only_generated_documents_can_be_downloaded(tmp_path):
    client, _ = make_client(tmp_path)
    doc_id = client.get("/api/documents").json()[0]["id"]  # the PRDv1 input
    assert client.get(f"/api/files/{doc_id}").status_code == 404
    assert client.get("/api/files/999").status_code == 404
