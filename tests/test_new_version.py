"""T12a: new-version PRD generation after manual add/update/delete."""
import shutil
from pathlib import Path

import pytest

from src.core.chunking.chunking import chunk_markdown
from src.infrastructure.db.db import connect, init_schema
from src.core.extraction.extraction import extract_requirements
from src.core.ingest.ingest import ingest_document
from src.core.manual.manual import add_requirement, delete_requirement, update_requirement
from src.core.manual.new_version import generate_new_version

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


@pytest.fixture
def env(tmp_path):
    src = tmp_path / "in" / "PRDv1.md"  # copy, so the original sample can never change
    src.parent.mkdir()
    shutil.copy(SAMPLES / "PRDv1.md", src)
    conn = connect(tmp_path / "t.db")
    init_schema(conn)
    ingest_document(conn, src, "lee", "kim")
    yield conn, src, tmp_path / "out"
    conn.close()


def _reqs(path):
    return {r.req_key: (r.title, r.content)
            for r in extract_requirements(chunk_markdown(Path(path).read_text(encoding="utf-8")))}


def test_new_version_reflects_update_delete_add_and_keeps_rest(env):
    conn, src, out = env
    original_bytes = src.read_bytes()
    v1 = _reqs(src)

    update_requirement(conn, "REQ-003", "투표 기간은 최대 14일로 제한한다.", "park", "choi")
    delete_requirement(conn, "REQ-006", "park", "choi")
    add_requirement(conn, "투표 공유", "관리자는 투표 링크를 공유할 수 있어야 한다.", "park", "choi")

    r = generate_new_version(conn, out)

    assert r.path == out / "PRDv2.md" and (r.name, r.version) == ("PRD", 2)
    v2 = _reqs(r.path)
    assert v2["REQ-003"][1] == "투표 기간은 최대 14일로 제한한다."
    assert "REQ-006" not in v2
    assert v2["REQ-009"] == ("투표 공유", "관리자는 투표 링크를 공유할 수 있어야 한다.")
    for key in ("REQ-001", "REQ-002", "REQ-004", "REQ-005", "REQ-007", "REQ-008"):
        assert v2[key] == v1[key]  # untouched requirements are identical
    assert len(v2) == 8 and r.requirement_count == 8

    text = r.path.read_text(encoding="utf-8")
    assert "# 정기투표앱 PRD v2" in text and "**문서 버전:** v2" in text
    assert "## 4. 비기능 요구사항" in text  # non-requirement sections survive
    assert text.index("REQ-009") < text.index("## 4. 비기능 요구사항")
    assert src.read_bytes() == original_bytes  # original input untouched

    doc = conn.execute("SELECT * FROM documents WHERE name='PRD' AND version=2").fetchone()
    assert doc["kind"] == "generated" and doc["source_path"] == str(r.path)


def test_same_changes_do_not_create_another_version(env):
    conn, _, out = env
    update_requirement(conn, "REQ-003", "투표 기간은 최대 14일이다.", "park", "choi")
    generate_new_version(conn, out)
    with pytest.raises(ValueError, match="수동으로 바뀐"):
        generate_new_version(conn, out)
    assert sorted(p.name for p in out.iterdir()) == ["PRDv2.md"]


def test_next_changes_build_on_latest_generated_version(env):
    conn, _, out = env
    update_requirement(conn, "REQ-003", "투표 기간은 최대 14일이다.", "park", "choi")
    generate_new_version(conn, out)
    delete_requirement(conn, "REQ-008", "park", "choi")
    r = generate_new_version(conn, out)  # source is the generated PRDv2.md
    assert r.version == 3 and "REQ-008" not in _reqs(r.path)
    assert _reqs(r.path)["REQ-003"][1] == "투표 기간은 최대 14일이다."


def test_no_manual_change_creates_nothing(env):
    conn, _, out = env
    with pytest.raises(ValueError, match="수동으로 바뀐"):
        generate_new_version(conn, out)
    assert not out.exists()


def test_empty_db_is_rejected(tmp_path):
    conn = connect(tmp_path / "e.db")
    init_schema(conn)
    with pytest.raises(ValueError, match="입력된 문서"):
        generate_new_version(conn, tmp_path / "out")
    conn.close()


def test_existing_file_is_not_overwritten(env):
    conn, _, out = env
    update_requirement(conn, "REQ-003", "투표 기간은 최대 14일이다.", "park", "choi")
    out.mkdir()
    (out / "PRDv2.md").write_text("keep me", encoding="utf-8")
    with pytest.raises(ValueError, match="덮어쓰지"):
        generate_new_version(conn, out)
    assert (out / "PRDv2.md").read_text(encoding="utf-8") == "keep me"
    assert conn.execute("SELECT COUNT(*) FROM documents WHERE kind='generated'").fetchone()[0] == 0


def test_original_sample_file_unchanged_by_generation(tmp_path):
    """Works straight from samples/ too, and leaves it byte-identical."""
    before = (SAMPLES / "PRDv1.md").read_bytes()
    conn = connect(tmp_path / "s.db")
    init_schema(conn)
    ingest_document(conn, SAMPLES / "PRDv1.md", "lee", "kim")
    delete_requirement(conn, "REQ-006", "park", "choi")
    generate_new_version(conn, tmp_path / "out")
    conn.close()
    assert (SAMPLES / "PRDv1.md").read_bytes() == before


# ---- change info (requester / modifier / modified date) written into the document ----

def test_changed_requirements_carry_requester_modifier_and_date(env):
    from src.core.chunking.chunking import split_change_info

    conn, _, out = env
    update_requirement(conn, "REQ-003", "투표 기간은 최대 14일로 제한한다.", "park", "choi")
    add_requirement(conn, "투표 공유", "관리자는 투표 링크를 공유할 수 있어야 한다.", "han", "seo")
    delete_requirement(conn, "REQ-006", "park", "choi")
    r = generate_new_version(conn, out)
    today = conn.execute("SELECT date('now')").fetchone()[0]

    chunks = {c.heading.split()[0]: c for c in chunk_markdown(r.path.read_text(encoding="utf-8"))
              if c.heading.startswith("REQ-")}
    assert chunks["REQ-003"].meta == {"requester": "park", "modifier": "choi", "modified_at": today}
    assert chunks["REQ-009"].meta == {"requester": "han", "modifier": "seo", "modified_at": today}
    assert "**요청자:** park" in chunks["REQ-003"].content  # the DB chunk text keeps it too
    for key in ("REQ-001", "REQ-002", "REQ-004", "REQ-005", "REQ-007", "REQ-008"):
        assert chunks[key].meta == {}  # untouched: nothing added
    # extraction returns the plain content and the info separately
    req = {x.req_key: x for x in extract_requirements(chunk_markdown(r.path.read_text(encoding="utf-8")))}
    assert req["REQ-003"].content == "투표 기간은 최대 14일로 제한한다."
    assert req["REQ-003"].change_info["modifier"] == "choi"
    assert split_change_info(chunks["REQ-001"].content)[1] == {}


def test_deleted_requirement_is_listed_with_who_and_when(env):
    conn, _, out = env
    delete_requirement(conn, "REQ-006", "park", "choi")
    text = generate_new_version(conn, out).path.read_text(encoding="utf-8")
    today = conn.execute("SELECT date('now')").fetchone()[0]
    assert "## 삭제된 요구사항" in text
    assert f"- REQ-006 투표 마감 알림 (삭제일 {today}, 요청자 park, 수정자 choi)" in text


def test_info_is_not_duplicated_in_later_versions(env):
    conn, _, out = env
    update_requirement(conn, "REQ-003", "투표 기간은 최대 14일이다.", "park", "choi")
    delete_requirement(conn, "REQ-006", "park", "choi")
    generate_new_version(conn, out)
    delete_requirement(conn, "REQ-008", "han", "seo")
    v3 = generate_new_version(conn, out).path.read_text(encoding="utf-8")
    assert v3.count("**요청자:** park") == 1  # REQ-003 once, not repeated
    assert v3.count("## 삭제된 요구사항") == 1
    assert "REQ-006" in v3 and "REQ-008" in v3 and "수정자 seo" in v3
    assert _reqs(out / "PRDv3.md")["REQ-003"][1] == "투표 기간은 최대 14일이다."
