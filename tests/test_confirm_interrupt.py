"""CRUD actions pause with interrupt() and run only after the user confirms."""
from interrupt_helpers import FakeStore, ScriptedModel, run_agent, status


# ---- CRUD confirmation -------------------------------------------------------

def test_delete_declined_changes_nothing(tmp_path):
    m = ScriptedModel("delete_requirement", {"req_key": "REQ-006", "requester": "a", "modifier": "b"})
    _, tools, seen = run_agent(tmp_path, m, FakeStore(), [False])
    assert seen[0]["kind"] == "confirm" and "REQ-006" in seen[0]["message"]
    assert "취소" in tools[0]
    row, count = status(tmp_path, "REQ-006")
    assert row["status"] == "active" and count == 8  # no history row added


def test_delete_confirmed_deletes(tmp_path):
    m = ScriptedModel("delete_requirement", {"req_key": "REQ-006", "requester": "a", "modifier": "b"})
    _, tools, _ = run_agent(tmp_path, m, FakeStore(), [True])
    assert "삭제 완료" in tools[0]
    assert status(tmp_path, "REQ-006")[0]["status"] == "deleted"


def test_update_and_add_need_confirmation(tmp_path):
    up = ScriptedModel("update_requirement",
                       {"req_key": "REQ-003", "content": "최대 10일", "requester": "a", "modifier": "b"})
    _, tools, seen = run_agent(tmp_path, up, FakeStore(), [False])
    assert seen[0]["kind"] == "confirm" and "취소" in tools[0]
    assert "7일" in status(tmp_path, "REQ-003")[0]["content"]



def test_add_declined_saves_nothing(tmp_path):
    add = ScriptedModel("add_requirement",
                        {"title": "새", "content": "내용", "requester": "a", "modifier": "b"})
    store = FakeStore()
    _, tools, seen = run_agent(tmp_path, add, store, [False])
    assert seen[0]["kind"] == "confirm" and "취소" in tools[0] and store.added == []
    assert status(tmp_path, "REQ-009")[0] is None


def test_invalid_actors_rejected_before_asking(tmp_path):
    m = ScriptedModel("delete_requirement", {"req_key": "REQ-006", "requester": "", "modifier": "b"})
    _, tools, seen = run_agent(tmp_path, m, FakeStore(), [])
    assert seen == [] and tools[0].startswith("오류")  # no confirmation prompt for a doomed request


def test_cli_respond_maps_console_input(monkeypatch):
    from main import cli_respond

    answers = iter(["y", "n", "9", "2", "새 질문"])
    monkeypatch.setattr("builtins.input", lambda _="": next(answers))
    assert cli_respond({"kind": "confirm", "message": "m"}) is True
    assert cli_respond({"kind": "confirm", "message": "m"}) is False
    opts = [{"id": "similar", "label": "a"}, {"id": "rephrase", "label": "b"}]
    assert cli_respond({"kind": "choice", "message": "m", "options": opts}) == "rephrase"  # "9" rejected, "2" taken
    assert cli_respond({"kind": "input", "message": "m"}) == "새 질문"
