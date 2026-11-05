"""T15: accuracy over the 10-question set (real Bedrock: 1 QA call + 1 query embedding each).

A question passes only if the answer is found, contains the expected keywords, and the
sources include the expected requirement with the right document, version, requester
and modifier. The summary line shows how many passed; all 10 must pass.
"""
from pathlib import Path

import pytest

from core.ingest.ingest import ingest_document
from core.qa.qa import ask
from infra.db.db import connect, init_schema
from infra.indexing.indexing import get_vectorstore, index_document
from qa_set import DOC_NAME, DOC_VERSION, MODIFIER, QUESTIONS, REQUESTER

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def _check(item, answer) -> str | None:
    """Return None if the question passed, otherwise the reason it failed."""
    if not answer.found:
        return f"찾지 못함: {answer.text}"
    for group in item["keywords"]:
        if not any(w in answer.text for w in group):
            return f"답변에 {group} 없음: {answer.text}"
    good = [s for s in answer.sources if s.req_key == item["req"]
            and (s.document_name, s.version) == (DOC_NAME, DOC_VERSION)
            and (s.requester, s.modifier) == (REQUESTER, MODIFIER)]
    if not good:
        got = sorted({(s.req_key, s.document_name, s.version, s.requester, s.modifier) for s in answer.sources})
        return f"출처에 {item['req']}({DOC_NAME} v{DOC_VERSION}, {REQUESTER}/{MODIFIER}) 없음: {got}"
    return None


@pytest.fixture(scope="module")
def results(tmp_path_factory):
    base = tmp_path_factory.mktemp("t15")
    conn = connect(base / "t15.db")
    init_schema(conn)
    r = ingest_document(conn, SAMPLES / "PRDv1.md", REQUESTER, MODIFIER)
    store = get_vectorstore(persist_dir=base / "chroma")
    index_document(conn, r.document_id, store)
    out = [(item, _check(item, ask(conn, item["q"], store=store))) for item in QUESTIONS]
    conn.close()
    passed = sum(1 for _, why in out if why is None)
    print(f"\n[T15] 출처까지 맞은 질문: {passed}/{len(out)}")
    for item, why in out:
        print(f"  {'OK  ' if why is None else 'FAIL'} {item['req']} {item['q']}" + (f"\n       -> {why}" if why else ""))
    return out


@pytest.mark.parametrize("n", range(len(QUESTIONS)), ids=[q["q"][:20] for q in QUESTIONS])
def test_question(results, n):
    item, why = results[n]
    assert why is None, f"{item['q']} -> {why}"


def test_all_ten_pass(results):
    assert len(results) == 10
    assert all(why is None for _, why in results)
