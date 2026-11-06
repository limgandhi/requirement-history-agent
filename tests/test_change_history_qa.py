"""T28: accuracy of change-history questions (real Bedrock: 1 QA call + 1 query embedding each).

Data: PRDv1 (lee/kim) -> PRDv2 detected by the system (park/choi) -> hand changes (han/seo):
REQ-004 edited, REQ-008 deleted, REQ-011 added. A question passes only if it is answered, the
answer has the expected keywords, and a source row matches the expected history entry.
"""
from pathlib import Path

import pytest

from src.core.change_detection.changes import ingest_new_version
from src.core.ingest.ingest import ingest_document
from src.core.manual.manual import add_requirement, delete_requirement, update_requirement
from src.core.qa.qa import ask
from src.infrastructure.db.db import connect, init_schema
from src.infrastructure.indexing.indexing import get_vectorstore, index_document

SAMPLES = Path(__file__).resolve().parent.parent / "samples"

# (question, req, action, source_type, requester, modifier, document, keyword groups)
QUESTIONS = [
    ("정기 투표 생성 요구사항(REQ-003)의 투표 기간 제한을 7일에서 14일로 바꾼 것은 누가 요청하고 누가 수정했나?",
     "REQ-003", "update", "auto", "park", "choi", ("PRD", 2), [["park"], ["choi"]]),
    ("투표 마감 알림 요구사항은 누가 삭제했나?",
     "REQ-006", "delete", "auto", "park", "choi", ("PRD", 2), [["choi"]]),
    ("익명 투표 옵션 요구사항은 어느 문서 버전에서 추가됐나?",
     "REQ-009", "add", "auto", "park", "choi", ("PRD", 2), [["v2", "V2", "PRDv2", "버전 2", "2"]]),
    ("소셜 로그인 요구사항은 누가 요청했나?",
     "REQ-010", "add", "auto", "park", "choi", ("PRD", 2), [["park"]]),
    ("반복 주기 설정 요구사항을 수동으로 수정한 사람은 누구인가?",
     "REQ-004", "update", "manual", "han", "seo", ("PRD", 1), [["seo"]]),
    ("투표 이력 조회(REQ-008) 요구사항은 누가 삭제했나?",
     "REQ-008", "delete", "manual", "han", "seo", ("PRD", 1), [["seo"]]),
    ("수동으로 추가한 REQ-011 요구사항은 무슨 내용인가?",
     "REQ-011", "add", "manual", "han", "seo", ("수동 입력", None), [["공유"]]),
    ("지금 정기 투표의 투표 기간은 최대 며칠인가?",
     "REQ-003", "update", "auto", "park", "choi", ("PRD", 2), [["14일"]]),
    ("REQ-003의 처음 투표 기간 값은 얼마였나?",
     "REQ-003", "add", "auto", "lee", "kim", ("PRD", 1), [["7일"]]),
    ("투표 마감 알림 요구사항은 아직 유효한가?",
     "REQ-006", "delete", "auto", "park", "choi", ("PRD", 2), [["삭제"]]),
]


def _check(item, answer) -> str | None:
    _, req, action, source_type, requester, modifier, (doc, version), keywords = item
    if not answer.found:
        return f"찾지 못함: {answer.text}"
    for group in keywords:
        if not any(w in answer.text for w in group):
            return f"답변에 {group} 없음: {answer.text}"
    good = [s for s in answer.sources if (s.req_key, s.action, s.source_type, s.requester, s.modifier,
                                          s.document_name, s.version) == (req, action, source_type, requester,
                                                                          modifier, doc, version)]
    if not good:
        got = sorted({(s.req_key, s.action, s.source_type, s.requester, s.modifier, s.document_name, s.version)
                      for s in answer.sources})
        return f"출처에 {(req, action, source_type, requester, modifier, doc, version)} 없음: {got}"
    return None


@pytest.fixture(scope="module")
def results(tmp_path_factory):
    base = tmp_path_factory.mktemp("t28")
    conn = connect(base / "t28.db")
    init_schema(conn)
    r1 = ingest_document(conn, SAMPLES / "PRDv1.md", "lee", "kim")
    store = get_vectorstore(persist_dir=base / "chroma")
    index_document(conn, r1.document_id, store)
    ingest_new_version(conn, SAMPLES / "PRDv2.md", "park", "choi", store=store)
    update_requirement(conn, "REQ-004", "관리자는 투표를 매주, 매월 또는 매분기 반복하도록 설정할 수 있어야 한다.",
                       "han", "seo", store=store)
    delete_requirement(conn, "REQ-008", "han", "seo", store=store)
    add_requirement(conn, "투표 공유", "관리자는 투표 링크를 공유할 수 있어야 한다.", "han", "seo", store=store)

    out = [(item, _check(item, ask(conn, item[0], store=store))) for item in QUESTIONS]
    conn.close()
    passed = sum(1 for _, why in out if why is None)
    print(f"\n[T28] 출처까지 맞은 질문: {passed}/{len(out)}")
    for item, why in out:
        print(f"  {'OK  ' if why is None else 'FAIL'} {item[1]} {item[0]}" + (f"\n       -> {why}" if why else ""))
    return out


@pytest.mark.parametrize("n", range(len(QUESTIONS)), ids=[q[0][:18] for q in QUESTIONS])
def test_question(results, n):
    item, why = results[n]
    assert why is None, f"{item[0]} -> {why}"


def test_all_ten_pass(results):
    assert len(results) == 10 and all(why is None for _, why in results)
