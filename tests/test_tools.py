"""Tool wrappers: actor validation and error-as-text, with no Bedrock calls."""
from src.interfaces.tools.tools import build_tools


def _tools(tmp_path):
    ts = build_tools(tmp_path / "t.db", tmp_path / "chroma")
    return {t.name: t for t in ts}


def test_tool_names(tmp_path):
    assert set(_tools(tmp_path)) == {
        "ingest_document", "ask_history", "add_requirement",
        "update_requirement", "delete_requirement", "create_new_version"}


def test_empty_actors_rejected_without_creating_db(tmp_path):
    t = _tools(tmp_path)
    out = t["add_requirement"].invoke({"title": "a", "content": "b", "requester": "", "modifier": "x"})
    assert out.startswith("오류")
    assert not (tmp_path / "t.db").exists()


def test_missing_db_is_reported_as_text(tmp_path):
    t = _tools(tmp_path)
    assert t["ask_history"].invoke({"question": "q"}).startswith("오류")
    assert t["create_new_version"].invoke({}).startswith("오류")
