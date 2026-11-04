"""T4: requirement extraction."""
from pathlib import Path

import pytest

from chunking.chunking import chunk_file, chunk_markdown
from extraction.extraction import (
    Requirement,
    _ExtractedItem,
    _ExtractedList,
    extract_requirements,
)

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


class FakeLLM:
    """Stands in for Bedrock; records whether it was called."""

    def __init__(self, titles):
        self.titles = titles
        self.calls = 0

    def with_structured_output(self, schema):
        return self

    def invoke(self, prompt):
        self.calls += 1
        return _ExtractedList(
            requirements=[_ExtractedItem(title=t, content=f"{t} 본문") for t in self.titles]
        )


def test_prdv1_ids_and_titles_match_document():
    reqs = extract_requirements(chunk_file(SAMPLES / "PRDv1.md"))
    assert [r.req_key for r in reqs] == [f"REQ-{n:03d}" for n in range(1, 9)]
    by_key = {r.req_key: r for r in reqs}
    assert by_key["REQ-003"].title == "정기 투표 생성"
    assert "7일" in by_key["REQ-003"].content
    assert all(isinstance(r, Requirement) for r in reqs)


def test_prdv2_keeps_document_ids_including_gaps():
    reqs = extract_requirements(chunk_file(SAMPLES / "PRDv2.md"))
    keys = [r.req_key for r in reqs]
    assert "REQ-006" not in keys
    assert keys == [
        "REQ-001", "REQ-002", "REQ-003", "REQ-004", "REQ-005",
        "REQ-007", "REQ-008", "REQ-009", "REQ-010",
    ]


def test_id_based_extraction_does_not_call_llm():
    llm = FakeLLM(["x"])
    extract_requirements(chunk_file(SAMPLES / "PRDv1.md"), llm=llm)
    assert llm.calls == 0


def test_duplicate_ids_are_rejected():
    chunks = chunk_markdown("### REQ-001 가입\n\n본문\n\n### REQ-001 로그인\n\n본문\n")
    with pytest.raises(ValueError, match="REQ-001"):
        extract_requirements(chunks)


def test_without_ids_llm_results_are_numbered_in_order():
    chunks = chunk_markdown("## 기능\n\n가입한다\n\n## 알림\n\n알림을 보낸다\n")
    llm = FakeLLM(["회원 가입", "알림 발송"])
    reqs = extract_requirements(chunks, llm=llm)
    assert llm.calls == 1
    assert [(r.req_key, r.title) for r in reqs] == [
        ("REQ-001", "회원 가입"),
        ("REQ-002", "알림 발송"),
    ]


def test_empty_document_gives_nothing_and_no_llm_call():
    llm = FakeLLM(["x"])
    assert extract_requirements([], llm=llm) == []
    assert llm.calls == 0


def test_real_llm_extracts_from_document_without_ids():
    text = (
        "# 사내 쿠폰 앱 기획\n\n작성자: 기획팀\n\n"
        "## 로그인\n\n사용자는 사내 계정으로 로그인할 수 있어야 한다. 비밀번호는 암호화해서 저장한다.\n\n"
        "## 쿠폰 발급\n\n관리자는 쿠폰을 만들어 구성원에게 발급할 수 있어야 한다.\n\n"
        "## 쿠폰 사용\n\n구성원은 발급받은 쿠폰을 한 번만 사용할 수 있어야 한다.\n"
    )
    reqs = extract_requirements(chunk_markdown(text))
    assert len(reqs) >= 3
    assert [r.req_key for r in reqs] == [f"REQ-{n:03d}" for n in range(1, len(reqs) + 1)]
    assert any("비밀번호" in r.content for r in reqs)
