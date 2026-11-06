"""T26/T27: change detection through the agent (summary + one confirmation) and name/version conflicts."""
import shutil
from pathlib import Path

import pytest

from src.core.change_detection.changes import ingest_new_version, plan_new_version
from src.core.ingest.ingest import ingest_document
from src.core.manual.manual import update_requirement
from src.core.manual.new_version import generate_new_version
from src.core.service import service
from src.infrastructure.db.db import connect, init_schema
from interrupt_helpers import FakeStore, ScriptedModel, run_agent, status

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
V2 = str(SAMPLES / "PRDv2.md")


def _ingest_args(path=V2, requester="park", modifier="choi"):
    return {"path": path, "requester": requester, "modifier": modifier}


# ---- T26: summary shown in the confirmation, saved only after yes ------------------

def test_prdv2_through_the_agent_shows_the_summary_and_saves_on_yes(tmp_path):
    _, tools, seen = run_agent(tmp_path, ScriptedModel("ingest_document", _ingest_args()), FakeStore(), [True])
    assert len(seen) == 1 and seen[0]["kind"] == "confirm"
    msg = seen[0]["message"]
    assert "추가 2 / 변경 1 / 삭제 1 / 그대로 6" in msg
    assert "추가 REQ-009" in msg and "변경 REQ-003" in msg and "삭제 REQ-006" in msg and "park" in msg
    assert "입력 완료" in tools[0] and "변경 감지" in tools[0]
    row, count = status(tmp_path, "REQ-006")
    assert row["status"] == "deleted" and count == 8 + 4


def test_declining_saves_nothing(tmp_path):
    _, tools, _ = run_agent(tmp_path, ScriptedModel("ingest_document", _ingest_args()), FakeStore(), [False])
    assert "취소" in tools[0]
    row, count = status(tmp_path, "REQ-006")
    assert row["status"] == "active" and count == 8
    conn = connect(tmp_path / "t.db")
    assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1
    conn.close()


def test_empty_actors_rejected_before_the_summary(tmp_path):
    _, tools, seen = run_agent(tmp_path, ScriptedModel("ingest_document", _ingest_args(requester="")), FakeStore(), [])
    assert seen == [] and tools[0].startswith("오류") and "필수" in tools[0]


def test_same_version_is_rejected_before_asking(tmp_path):
    _, tools, seen = run_agent(
        tmp_path, ScriptedModel("ingest_document", _ingest_args(path=str(SAMPLES / "PRDv1.md"))), FakeStore(), [])
    assert seen == [] and tools[0].startswith("오류") and "이미 입력된 문서" in tools[0]


def test_a_version_without_changes_is_reported_before_asking(tmp_path):
    same = tmp_path / "PRDv2.md"
    shutil.copy(SAMPLES / "PRDv1.md", same)
    _, tools, seen = run_agent(tmp_path, ScriptedModel("ingest_document", _ingest_args(path=str(same))), FakeStore(), [])
    assert seen == [] and "바뀐 요구사항이 없어" in tools[0]


def test_upload_folder_limit(tmp_path):
    from src.interfaces.agent.agent import build_agent, run_turn
    make = __import__("interrupt_helpers").make_db
    make(tmp_path)
    allowed = tmp_path / "uploads"
    allowed.mkdir()
    agent = build_agent(tmp_path / "t.db", tmp_path / "chroma", llm=ScriptedModel("ingest_document", _ingest_args()),
                        store=FakeStore(), output_dir=tmp_path / "out", allowed_input_dirs=[allowed])
    cfg = {"configurable": {"thread_id": "x"}}
    run_turn(agent, "go", cfg, lambda payload: True)
    texts = [m.content for m in agent.get_state(cfg).values["messages"] if m.type == "tool"]
    assert "허용된 폴더" in texts[0]  # samples/PRDv2.md is outside the allowed folder


# ---- T27: manual changes, generated documents, re-input ----------------------------

@pytest.fixture
def env(tmp_path):
    conn = connect(tmp_path / "t.db")
    init_schema(conn)
    ingest_document(conn, SAMPLES / "PRDv1.md", "lee", "kim")
    yield conn, tmp_path
    conn.close()


def test_a_generated_version_blocks_the_same_name_and_version_with_a_clear_message(env):
    conn, base = env
    update_requirement(conn, "REQ-003", "투표 기간은 최대 10일이다.", "han", "seo")
    generate_new_version(conn, base / "out")  # generated PRD v2
    with pytest.raises(ValueError, match="시스템이 수동 변경을 반영해 만든 문서.*PRDv3"):
        plan_new_version(conn, SAMPLES / "PRDv2.md")
    with pytest.raises(ValueError, match="시스템이 수동 변경을 반영해 만든 문서"):
        service.preview_ingest(base / "t.db", SAMPLES / "PRDv2.md")


def test_reinputting_the_generated_file_as_a_higher_version_finds_no_changes(env):
    conn, base = env
    update_requirement(conn, "REQ-003", "투표 기간은 최대 10일이다.", "han", "seo")
    r = generate_new_version(conn, base / "out")
    again = base / "PRDv3.md"
    shutil.copy(r.path, again)  # the file already carries requester/modifier/date lines
    with pytest.raises(ValueError, match="바뀐 요구사항이 없어"):
        ingest_new_version(conn, again, "a", "b", store=FakeStore())


def test_editing_the_generated_file_by_hand_is_detected(env):
    conn, base = env
    update_requirement(conn, "REQ-003", "투표 기간은 최대 10일이다.", "han", "seo")
    r = generate_new_version(conn, base / "out")
    edited = base / "PRDv3.md"
    edited.write_text(r.path.read_text(encoding="utf-8").replace("이메일과 비밀번호", "이메일과 비밀번호(10자 이상)"),
                      encoding="utf-8")
    result = ingest_new_version(conn, edited, "park", "choi", store=FakeStore())
    d = result.detection
    assert [c.req_key for c in d.changed] == ["REQ-001"]  # only the hand edit; REQ-003 is already current
    assert "REQ-003" in d.unchanged and d.added == [] and d.deleted == []
    h = conn.execute("SELECT requester, modifier FROM requirement_history WHERE source_type='auto' AND action='update'").fetchone()
    assert (h["requester"], h["modifier"]) == ("park", "choi")  # REQ-001 carries no info line: the input values
