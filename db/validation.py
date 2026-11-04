"""Single place that enforces the required requester/modifier rule.

Every write path (document ingest, manual add/update/delete) must call
require_actors() before touching the database, files, the LLM or Chroma.
"""


def _is_blank(value) -> bool:
    # Anything that is not a string (None, numbers, ...) counts as missing.
    return not isinstance(value, str) or not value.strip()


def require_actors(requester: str | None, modifier: str | None) -> tuple[str, str]:
    """Return stripped (requester, modifier), or raise ValueError if either is empty."""
    missing = []
    if _is_blank(requester):
        missing.append("요청자(requester)")
    if _is_blank(modifier):
        missing.append("수정자(modifier)")
    if missing:
        raise ValueError(f"{', '.join(missing)}는 필수입니다. 저장하지 않았습니다.")
    return requester.strip(), modifier.strip()
