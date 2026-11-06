"""T23: compare a new document version with the current requirements (pure, no I/O).

Rules (decided in T22, see SERVICE.md):
- The comparison base is the current active requirements in the DB.
- An item matches when its document ID equals an active requirement's ID.
- Matched items are 'changed' when the normalized title or content differs
  (runs of whitespace are ignored); otherwise they are left alone.
- Everything that does not match is an addition. It keeps the ID written in the
  document if that ID was never used in the DB; if it has no ID, or the ID belongs
  to a deleted requirement (deleted IDs are never reused), it gets the next number.
- Active requirements that no item matched are deletions.
- A document without IDs cannot be matched, so all items are additions and nothing
  is treated as deleted.
"""
import re
from dataclasses import dataclass, field

_REQ_KEY = re.compile(r"^REQ-(\d+)$")


@dataclass
class Item:
    """One requirement as written in the new document."""
    doc_key: str | None  # ID written in the document; None when the document has no IDs
    title: str
    content: str
    actors: tuple[str, str] | None = None  # (requester, modifier) written in the document, if any


@dataclass
class Change:
    kind: str  # 'add' | 'update' | 'delete'
    req_key: str  # ID used in the DB
    title: str  # new title (old title for a deletion)
    content: str  # new content (old content for a deletion)
    doc_key: str | None = None  # ID in the document when it differs from req_key
    old_title: str | None = None
    old_content: str | None = None
    actors: tuple[str, str] | None = None


@dataclass
class Detection:
    added: list[Change] = field(default_factory=list)
    changed: list[Change] = field(default_factory=list)
    deleted: list[Change] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    renumbered: list[tuple[str, str]] = field(default_factory=list)  # (ID in document, ID in DB)
    id_less: bool = False

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.changed or self.deleted)


def normalize(text: str) -> str:
    return " ".join(text.split())


def detect_changes(
    active: dict[str, tuple[str, str]],
    known_keys: set[str],
    items: list[Item],
    id_less: bool = False,
) -> Detection:
    """active: req_key -> (title, content) of current requirements.
    known_keys: every req_key the DB has ever had (active and deleted)."""
    result = Detection(id_less=id_less)
    numbers = [int(m.group(1)) for k in known_keys if (m := _REQ_KEY.match(k))]
    next_number = max(numbers, default=0) + 1
    used = set(known_keys)
    if not id_less:  # IDs the DB has never seen are reserved first, so a new number never takes one of them
        used |= {i.doc_key for i in items if i.doc_key}
    matched: set[str] = set()

    for item in items:
        key = None if id_less else item.doc_key
        if key in active:
            matched.add(key)
            old_title, old_content = active[key]
            if normalize(old_title) == normalize(item.title) and normalize(old_content) == normalize(item.content):
                result.unchanged.append(key)
            else:
                result.changed.append(Change(
                    "update", key, item.title, item.content,
                    old_title=old_title, old_content=old_content, actors=item.actors))
            continue

        if key is not None and key not in known_keys:
            new_key = key  # an ID the DB has never seen: keep the document's own ID
        else:
            while f"REQ-{next_number:03d}" in used:
                next_number += 1
            new_key = f"REQ-{next_number:03d}"
            if item.doc_key is not None and not id_less:
                result.renumbered.append((item.doc_key, new_key))
        used.add(new_key)
        result.added.append(Change(
            "add", new_key, item.title, item.content,
            doc_key=item.doc_key if item.doc_key != new_key else None, actors=item.actors))

    if not id_less:
        for key, (title, content) in active.items():
            if key not in matched:
                result.deleted.append(Change("delete", key, title, content, old_title=title, old_content=content))
    return result
