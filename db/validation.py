"""Single place that enforces the required requester/modifier rule.

Every write path (document ingest now; manual add/update/delete later) must call
require_actors() before touching the database.
"""


def require_actors(requester: str | None, modifier: str | None) -> tuple[str, str]:
    """Return stripped (requester, modifier), or raise ValueError if either is empty."""
    missing = []
    if not (requester or "").strip():
        missing.append("요청자(requester)")
    if not (modifier or "").strip():
        missing.append("수정자(modifier)")
    if missing:
        raise ValueError(f"{', '.join(missing)}는 필수입니다. 저장하지 않았습니다.")
    return requester.strip(), modifier.strip()
