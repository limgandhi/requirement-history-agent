"""Question answering over requirement history.

Flow: search chunks (Chroma) -> map hits to requirements and their history
(SQLite) -> LLM answers from that context only. Sources are built by code from
SQLite rows, never from LLM text, so document/version/requester/modifier can't
be invented. The LLM only says which requirement keys it relied on.
"""
import re
import sqlite3
from dataclasses import dataclass, field

from pydantic import BaseModel

from infra.indexing.indexing import search_chunks

_PROMPT = """너는 요구사항 이력 질의응답 도우미다. 아래 [근거]에 있는 내용만으로 질문에 답해라.

규칙:
- [근거]에 답이 없으면 answerable을 false로 하고 answer에는 "찾지 못했다"고 써라. 추측하거나 지어내지 마라.
- 현재 값과 이력이 다르면 질문이 묻는 시점에 맞게 답하되, 이력의 변경 내용을 근거로 해라.
- 상태가 deleted인 요구사항은 현재 요구사항이 아니다. 현재 값을 묻는 질문에는 그 내용을 현재 규칙처럼 답하지 말고 삭제되었다고 알려라.
  삭제·변경 이력을 묻는 질문에는 이력을 근거로 답해라.
- 변경 이력을 답할 때는 이력의 이전 값, 새 값, 문서 버전, 요청자, 수정자, 수동/자동 구분을 그대로 써라.
- used_req_keys에는 답에 실제로 사용한 요구사항 ID만 넣어라.

[근거]
{context}

[질문]
{question}
"""


class _LLMAnswer(BaseModel):
    answerable: bool
    answer: str
    used_req_keys: list[str]


@dataclass
class Source:
    req_key: str
    title: str
    document_name: str
    version: int | None  # None for requirements added by hand (no source document)
    requester: str
    modifier: str
    action: str
    source_type: str
    created_at: str


@dataclass
class Answer:
    text: str
    found: bool
    sources: list[Source] = field(default_factory=list)


_REQ_ID = re.compile(r"REQ-\d+", re.IGNORECASE)
# Words that show the user is asking about history (so a deleted requirement may be the answer).
_HISTORY_WORDS = ("삭제", "지웠", "지운", "제거", "폐기", "이력", "변경", "바뀌", "바꿨", "바꾼", "수정", "추가",
                  "언제", "누가", "누구", "요청자", "수정자", "버전", "유효", "아직", "예전", "처음")


_MIN_SHARED_WORDS = 2


def _history_keys(conn: sqlite3.Connection, question: str, limit: int = 2) -> list[str]:
    """Requirements the search index cannot give: the ones the question names by ID, and --
    for a history question -- deleted ones (their vectors are removed) that match by keywords."""
    keys: list[str] = []
    for found in _REQ_ID.findall(question):
        key = found.upper()
        if key not in keys and conn.execute("SELECT 1 FROM requirements WHERE req_key = ?", (key,)).fetchone():
            keys.append(key)
    if any(word in question for word in _HISTORY_WORDS):
        from infra.indexing.indexing import korean_tokens

        # Few requirements are deleted, so BM25 (whose weights need many documents) is no use here:
        # rank by how many of the question's words the title or content shares.
        question_words = set(korean_tokens(question))
        rows = conn.execute(
            "SELECT req_key, title, content FROM requirements WHERE status = 'deleted' ORDER BY id").fetchall()
        scored = sorted(
            ((len(question_words & set(korean_tokens(f"{r['title']} {r['content']}"))), r["req_key"]) for r in rows),
            reverse=True)
        for shared, key in scored[:limit]:
            if shared >= _MIN_SHARED_WORDS and key not in keys:
                keys.append(key)
    return keys


def _requirement_context(conn: sqlite3.Connection, req_key: str):
    req = conn.execute("SELECT * FROM requirements WHERE req_key = ?", (req_key,)).fetchone()
    if req is None:
        return None
    # Manual changes carry no document_id of their own: fall back to the document
    # the requirement came from, and to "수동 입력" when it was added by hand.
    history = conn.execute(
        "SELECT h.*, "
        "COALESCE(d.name, rd.name, '수동 입력') AS doc_name, "
        "COALESCE(d.version, rd.version) AS doc_version "
        "FROM requirement_history h "
        "JOIN requirements r ON r.id = h.requirement_id "
        "LEFT JOIN documents d ON d.id = h.document_id "
        "LEFT JOIN documents rd ON rd.id = r.document_id "
        "WHERE h.requirement_id = ? ORDER BY h.id",
        (req["id"],),
    ).fetchall()
    return req, history


def _render(req, history) -> str:
    lines = [f"{req['req_key']} {req['title']} (상태: {req['status']})", f"현재 내용: {req['content']}"]
    for h in history:
        lines.append(
            f"- 이력[{h['created_at']}] {h['action']}/{h['source_type']} "
            f"문서 {h['doc_name']}{'' if h['doc_version'] is None else ' v' + str(h['doc_version'])}, 요청자 {h['requester']}, 수정자 {h['modifier']}"
            f" | 이전: {h['old_value']} | 새 값: {h['new_value']}"
        )
    return "\n".join(lines)


def _sources(req, history) -> list[Source]:
    return [
        Source(
            req_key=req["req_key"], title=req["title"],
            document_name=h["doc_name"], version=h["doc_version"],
            requester=h["requester"], modifier=h["modifier"],
            action=h["action"], source_type=h["source_type"], created_at=h["created_at"],
        )
        for h in history
    ]


def ask(conn: sqlite3.Connection, question: str, store=None, llm=None, k: int = 4) -> Answer:
    hits = search_chunks(question, k=k, store=store)

    asked = _history_keys(conn, question)
    keys: list[str] = list(asked)
    for hit in hits:
        if hit.req_key and hit.req_key not in keys:
            keys.append(hit.req_key)

    contexts = {}
    for key in keys:
        ctx = _requirement_context(conn, key)
        # A deleted requirement is shown only when the question points at it (by ID or as a
        # history question); it is never presented as a current requirement.
        if ctx and (ctx[0]["status"] == "active" or key in asked):
            contexts[key] = ctx
    if not contexts:
        return Answer(text="관련 요구사항 출처를 찾지 못했다.", found=False)

    if llm is None:
        from infra.llm.llm import get_llm

        llm = get_llm()
    context = "\n\n".join(_render(*c) for c in contexts.values())
    result = llm.with_structured_output(_LLMAnswer).invoke(
        _PROMPT.format(context=context, question=question)
    )

    used = [k.upper() for k in result.used_req_keys if k.upper() in contexts]
    if not result.answerable or not used:
        return Answer(text=result.answer or "찾지 못했다.", found=False)
    sources = [s for key in used for s in _sources(*contexts[key])]
    return Answer(text=result.answer, found=True, sources=sources)


def format_answer(answer: Answer) -> str:
    """Plain-text rendering shared by any front end (CLI now, web UI later)."""
    lines = [answer.text]
    if answer.sources:
        lines.append("")
        lines.append("출처:")
        for s in answer.sources:
            doc = s.document_name if s.version is None else f"{s.document_name}v{s.version}"
            lines.append(
                f"- {s.req_key} {s.title} | {doc} | "
                f"요청자 {s.requester} | 수정자 {s.modifier} | {s.action}/{s.source_type} | {s.created_at}"
            )
    return "\n".join(lines)
