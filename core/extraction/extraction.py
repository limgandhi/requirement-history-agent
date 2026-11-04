"""Extract requirements from Markdown chunks.

Rules:
1. If the document has chunks whose heading is "REQ-<number> <title>", those
   IDs are used as-is (deterministic, no LLM call). Chunks without such a
   heading are ignored in that case.
2. If no chunk carries an ID, an LLM picks the requirements out of the chunks
   and they are numbered REQ-001, REQ-002, ... in document order.
"""
import re
from dataclasses import dataclass

from pydantic import BaseModel

from core.chunking.chunking import Chunk

_REQ_HEADING = re.compile(r"^(REQ-\d+)\s+(.+)$", re.IGNORECASE)

_PROMPT = """아래는 기획/요구사항 문서를 헤딩 단위로 나눈 조각들이다.
제품이 만족해야 하는 개별 요구사항만 추출해라.

규칙:
- 개요, 작성자, 문서 버전, 사용자 설명, 범위 밖 설명은 요구사항이 아니므로 제외한다.
- 문서에 없는 내용을 만들거나 합치지 마라. content는 문서의 문구를 최대한 그대로 쓴다.
- 요구사항이 하나도 없으면 빈 목록을 반환한다.
- 문서에 나온 순서를 유지한다.

--- 문서 조각 ---
{chunks}
"""


@dataclass
class Requirement:
    req_key: str
    title: str
    content: str
    position: int | None = None  # source chunk position; None when LLM-extracted


class _ExtractedItem(BaseModel):
    title: str
    content: str


class _ExtractedList(BaseModel):
    requirements: list[_ExtractedItem]


def _extract_by_id(chunks: list[Chunk]) -> list[Requirement]:
    found: list[Requirement] = []
    seen: set[str] = set()
    for chunk in chunks:
        match = _REQ_HEADING.match(chunk.heading)
        if not match:
            continue
        key = match.group(1).upper()
        if key in seen:
            raise ValueError(f"Duplicate requirement ID in document: {key}")
        seen.add(key)
        found.append(
            Requirement(
                req_key=key,
                title=match.group(2).strip(),
                content=chunk.content,
                position=chunk.position,
            )
        )
    return found


def _extract_with_llm(chunks: list[Chunk], llm=None) -> list[Requirement]:
    if not chunks:
        return []
    if llm is None:
        from infra.llm.llm import get_llm  # imported lazily so ID-based extraction needs no AWS

        llm = get_llm()

    rendered = "\n\n".join(f"[{' > '.join(c.path)}]\n{c.content}" for c in chunks)
    result = llm.with_structured_output(_ExtractedList).invoke(
        _PROMPT.format(chunks=rendered)
    )
    return [
        Requirement(req_key=f"REQ-{i:03d}", title=item.title, content=item.content)
        for i, item in enumerate(result.requirements, start=1)
    ]


def extract_requirements(chunks: list[Chunk], llm=None) -> list[Requirement]:
    keyed = _extract_by_id(chunks)
    if keyed:
        return keyed
    return _extract_with_llm(chunks, llm)
