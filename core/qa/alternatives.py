"""T13: alternatives offered when no source is found (UI-independent).

The kinds of alternatives (decided here):
- similar : show the closest current requirements (ID and title) so the user can
            pick one and ask again
- rephrase: the user types a new question and the search runs again
- cancel  : stop without an answer
The front end shows these options and sends the chosen id back.
"""
import sqlite3

from infra.indexing.indexing import search_chunks

OPTIONS = [
    {"id": "similar", "label": "비슷한 요구사항 후보 보기"},
    {"id": "rephrase", "label": "질문을 바꿔 다시 검색"},
    {"id": "cancel", "label": "그만두기"},
]

MAX_ROUNDS = 3  # questions asked in total (first one + rephrases) before giving up
_FALLBACK_LIST = 10  # how many requirements to list when the search gives no candidate


def similar_requirements(conn: sqlite3.Connection, question: str, store=None, k: int = 8) -> list[tuple[str, str]]:
    """Closest current (active) requirements as (req_key, title); falls back to the first few."""
    keys: list[str] = []
    for hit in search_chunks(question, k=k, store=store):
        if hit.req_key and hit.req_key not in keys:
            keys.append(hit.req_key)
    found = []
    for key in keys:
        row = conn.execute(
            "SELECT req_key, title FROM requirements WHERE req_key = ? AND status = 'active'", (key,)
        ).fetchone()
        if row:
            found.append((row["req_key"], row["title"]))
    if found:
        return found
    rows = conn.execute(
        "SELECT req_key, title FROM requirements WHERE status = 'active' ORDER BY id LIMIT ?",
        (_FALLBACK_LIST,),
    ).fetchall()
    return [(r["req_key"], r["title"]) for r in rows]


def format_candidates(candidates: list[tuple[str, str]]) -> str:
    if not candidates:
        return "후보로 보여 줄 요구사항이 없습니다."
    lines = ["비슷한 요구사항 후보:"] + [f"- {k} {t}" for k, t in candidates]
    lines.append("이 중 궁금한 요구사항을 골라 다시 질문해 주세요.")
    return "\n".join(lines)
