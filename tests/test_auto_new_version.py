"""T21: every saved add/update/delete also writes a new-version PRD file (after one y/N confirmation)."""
from pathlib import Path

import pytest

from interrupt_helpers import FakeStore, ScriptedModel, run_agent, status

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
ORIGINAL = (SAMPLES / "PRDv1.md").read_bytes()

UPDATE = {"req_key": "REQ-003", "content": "투표 기간은 최대 14일로 제한한다.", "requester": "park", "modifier": "choi"}
ADD = {"title": "투표 공유", "content": "관리자는 투표 링크를 공유할 수 있어야 한다.", "requester": "park", "modifier": "choi"}
DELETE = {"req_key": "REQ-006", "requester": "park", "modifier": "choi"}
CASES = [("update_requirement", UPDATE), ("add_requirement", ADD), ("delete_requirement", DELETE)]


def _files(out):
    return sorted(p.name for p in out.iterdir()) if out.exists() else []


@pytest.mark.parametrize("tool,args", CASES)
def test_confirmation_is_asked_once_and_mentions_new_version(tmp_path, tool, args):
    _, _, seen = run_agent(tmp_path, ScriptedModel(tool, args), FakeStore(), [False])
    assert len(seen) == 1 and seen[0]["kind"] == "confirm" and "새 버전" in seen[0]["message"]


@pytest.mark.parametrize("tool,args", CASES)
def test_saving_always_creates_a_new_version_file(tmp_path, tool, args):
    out = tmp_path / "out"
    _, tools, _ = run_agent(tmp_path, ScriptedModel(tool, args), FakeStore(), [True], out)
    assert "완료" in tools[0] and "새 버전 생성" in tools[0] and "PRDv2.md" in tools[0]
    assert _files(out) == ["PRDv2.md"]
    text = (out / "PRDv2.md").read_text(encoding="utf-8")
    if tool == "update_requirement":
        assert "14일" in text and "**수정자:** choi" in text
    elif tool == "add_requirement":
        assert "REQ-009 투표 공유" in text
    else:
        assert "REQ-006 투표 마감 알림 (삭제일" in text
    assert (SAMPLES / "PRDv1.md").read_bytes() == ORIGINAL  # original input untouched


def test_each_save_makes_its_own_file(tmp_path):
    """Two saves -> PRDv2.md then PRDv3.md (the DB and generated files are shared by path)."""
    out = tmp_path / "out"
    run_agent(tmp_path, ScriptedModel("update_requirement", UPDATE), FakeStore(), [True], out)
    # second change on the same DB: run_agent rebuilds the DB, so drive the service directly
    from core.service import service
    service.delete_requirement_manual(tmp_path / "t.db", tmp_path / "chroma", "REQ-006", "park", "choi", store=FakeStore())
    r = service.create_new_version(tmp_path / "t.db", out)
    assert r.version == 3 and _files(out) == ["PRDv2.md", "PRDv3.md"]


@pytest.mark.parametrize("tool,args", CASES)
def test_cancel_changes_nothing_anywhere(tmp_path, tool, args):
    out = tmp_path / "out"
    store = FakeStore()
    _, tools, _ = run_agent(tmp_path, ScriptedModel(tool, args), store, [False], out)
    assert "취소" in tools[0]
    assert status(tmp_path, "REQ-001")[1] == 8 and _files(out) == [] and store.added == []
    assert status(tmp_path, "REQ-003")[0]["content"].startswith("관리자는 투표 안건")


def test_empty_actors_rejected_before_the_confirmation(tmp_path):
    bad = dict(UPDATE, requester="")
    _, tools, seen = run_agent(tmp_path, ScriptedModel("update_requirement", bad), FakeStore(), [], tmp_path / "out")
    assert seen == [] and tools[0].startswith("오류") and _files(tmp_path / "out") == []


def test_new_version_failure_keeps_the_saved_change(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "PRDv2.md").write_text("keep me", encoding="utf-8")  # the file name is taken
    _, tools, _ = run_agent(tmp_path, ScriptedModel("delete_requirement", DELETE), FakeStore(), [True], out)
    assert "삭제 완료" in tools[0] and "만들지 못했습니다" in tools[0] and "변경은 저장됨" in tools[0]
    assert status(tmp_path, "REQ-006")[0]["status"] == "deleted"
    assert (out / "PRDv2.md").read_text(encoding="utf-8") == "keep me"
