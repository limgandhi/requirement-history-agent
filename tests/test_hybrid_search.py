"""T16: hybrid (BM25 + vector) search with a fake store (no Bedrock)."""
from langchain_core.documents import Document

from infra.indexing.indexing import search_chunks

DOCS = {
    "REQ-002": ("REQ-002 조직 및 구성원 관리\n관리자는 이메일로 구성원을 초대할 수 있다. 초대받은 사용자만 참여할 수 있다.", 2),
    "REQ-005": ("REQ-005 투표 참여\n투표자는 선택지 하나를 골라 투표한다. 한 번만 참여할 수 있다.", 5),
    "REQ-006": ("REQ-006 투표 마감 알림\n마감 24시간 전에 이메일 알림을 보낸다.", 6),
}


def _meta(key, n):
    return {"chunk_id": n, "document_id": 1, "document_name": "PRD", "version": 1,
            "heading": key, "req_key": key}


class FakeStore:
    """Vector side ranks a general section first and REQ-002 last (like the real failure)."""

    def similarity_search_with_score(self, query, k=4):
        order = ["overview", "REQ-005", "REQ-006", "REQ-002"]
        out = []
        for key in order:
            if key == "overview":
                out.append((Document(page_content="1. 개요\n정기 투표 앱", metadata={"heading": "1. 개요"}), 0.1))
            else:
                text, n = DOCS[key]
                out.append((Document(page_content=text, metadata=_meta(key, n)), 0.2))
        return out[:k]

    def get(self, include=None):
        keys = list(DOCS)
        return {"documents": [DOCS[k][0] for k in keys] + ["1. 개요\n정기 투표 앱"],
                "metadatas": [_meta(k, DOCS[k][1]) for k in keys] + [{"heading": "1. 개요"}]}


def test_general_sections_never_returned():
    for hybrid in (True, False):
        hits = search_chunks("투표", k=10, store=FakeStore(), hybrid=hybrid)
        assert hits and all(h.req_key for h in hits)


def test_keyword_match_lifts_requirement_the_vector_ranked_last():
    q = "초대받지 않은 사용자도 참여할 수 있나?"
    vector_only = [h.req_key for h in search_chunks(q, k=3, store=FakeStore(), hybrid=False)]
    hybrid = [h.req_key for h in search_chunks(q, k=3, store=FakeStore(), hybrid=True)]
    assert vector_only.index("REQ-002") == 2  # last in the vector ranking
    assert hybrid.index("REQ-002") < vector_only.index("REQ-002")  # BM25 ("초대") moved it up


def test_k_limits_results():
    assert len(search_chunks("투표", k=2, store=FakeStore())) == 2
