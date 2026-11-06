"""Heading-based Markdown chunking.

Every heading starts a new chunk whose body runs until the next heading.
Chunks with an empty body (e.g. a heading that only contains sub-headings)
are dropped. Lines inside fenced code blocks are never treated as headings.
"""
import re
from dataclasses import dataclass, field
from pathlib import Path

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")

# Change info that new-version PRDs (core/manual/new_version.py) append to a requirement:
#   - **요청자:** park
#   - **수정자:** choi
#   - **수정일:** 2026-11-05
CHANGE_INFO_LABELS = {"요청자": "requester", "수정자": "modifier", "수정일": "modified_at"}
_INFO_LINE = re.compile(r"^- \*\*(요청자|수정자|수정일):\*\*\s*(.+?)\s*$")


def split_change_info(content: str) -> tuple[str, dict]:
    """Split trailing change-info lines off a block: (text without them, info).

    Only a complete set (requester, modifier, modified date) counts; otherwise the
    content is returned unchanged with an empty info, so ordinary bullet lists are safe.
    """
    lines = content.rstrip().split("\n")
    info: dict = {}
    while lines and (m := _INFO_LINE.match(lines[-1])):
        info[CHANGE_INFO_LABELS[m.group(1)]] = m.group(2)
        lines.pop()
    if set(info) != set(CHANGE_INFO_LABELS.values()):
        return content, {}
    return "\n".join(lines).rstrip(), info


def format_change_info(requester: str, modifier: str, modified_at: str) -> str:
    return f"- **요청자:** {requester}\n- **수정자:** {modifier}\n- **수정일:** {modified_at}"


@dataclass
class Chunk:
    heading: str
    level: int
    path: list[str] = field(default_factory=list)  # heading hierarchy, root first
    position: int = 0  # order among emitted chunks, starting at 0
    content: str = ""  # full text, change-info lines included (so the DB keeps them)
    meta: dict = field(default_factory=dict)  # requester / modifier / modified_at when present


def chunk_markdown(text: str, keep_empty: bool = False) -> list[Chunk]:
    chunks: list[Chunk] = []
    stack: list[tuple[int, str]] = []  # (level, heading) of current ancestors
    current: Chunk | None = None
    body: list[str] = []
    in_fence = False

    def flush() -> None:
        nonlocal current, body
        if current is not None:
            content = "\n".join(body).strip()
            if content or keep_empty:  # keep_empty: rebuild a whole document
                current.content = content
                current.meta = split_change_info(content)[1]
                current.position = len(chunks)
                chunks.append(current)
        current, body = None, []

    for line in text.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
        match = None if in_fence else _HEADING.match(line)
        if match is None:
            body.append(line)
            continue

        flush()
        level, heading = len(match.group(1)), match.group(2)
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, heading))
        current = Chunk(heading=heading, level=level, path=[h for _, h in stack])

    flush()
    return chunks


def chunk_file(path: str | Path) -> list[Chunk]:
    return chunk_markdown(Path(path).read_text(encoding="utf-8"))
