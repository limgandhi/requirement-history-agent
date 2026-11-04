"""T3: heading-based chunking."""
from pathlib import Path

from chunking.chunking import chunk_file, chunk_markdown

SAMPLE_V1 = Path(__file__).resolve().parent.parent / "samples" / "PRDv1.md"


def _req_chunks(chunks):
    return [c for c in chunks if c.heading.startswith("REQ-")]


def test_prdv1_has_one_chunk_per_requirement():
    reqs = _req_chunks(chunk_file(SAMPLE_V1))
    ids = [c.heading.split()[0] for c in reqs]
    assert ids == [f"REQ-{n:03d}" for n in range(1, 9)]


def test_requirement_chunks_keep_their_own_body_only():
    reqs = {c.heading.split()[0]: c for c in _req_chunks(chunk_file(SAMPLE_V1))}
    assert "7일" in reqs["REQ-003"].content
    assert "REQ-004" not in reqs["REQ-003"].content
    assert "매주 또는 매월" in reqs["REQ-004"].content


def test_path_holds_heading_hierarchy():
    reqs = {c.heading.split()[0]: c for c in _req_chunks(chunk_file(SAMPLE_V1))}
    path = reqs["REQ-003"].path
    assert path[0].startswith("정기투표앱 PRD")
    assert path[1] == "3. 요구사항"
    assert path[-1] == reqs["REQ-003"].heading


def test_positions_are_sequential():
    chunks = chunk_file(SAMPLE_V1)
    assert [c.position for c in chunks] == list(range(len(chunks)))


def test_heading_without_body_is_dropped():
    chunks = chunk_markdown("## 3. 요구사항\n\n### REQ-001 가입\n\n본문\n")
    assert [c.heading for c in chunks] == ["REQ-001 가입"]


def test_hash_inside_code_fence_is_not_a_heading():
    text = "### REQ-001 예시\n\n```\n# 주석입니다\n```\n\n끝\n"
    chunks = chunk_markdown(text)
    assert len(chunks) == 1
    assert "# 주석입니다" in chunks[0].content


def test_empty_text_gives_no_chunks():
    assert chunk_markdown("") == []
