from datetime import datetime
from typing import Any


def serialize_doc(doc: dict | None) -> dict | None:
    """Mongo doc -> JSON-safe dict (ObjectId stripped, datetimes ISO)."""
    if doc is None:
        return None
    result: dict[str, Any] = {}
    for key, value in doc.items():
        if key == "_id":
            continue
        result[key] = _serialize_value(value)
    return result


def _serialize_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _serialize_value(v) for k, v in value.items() if k != "_id"}
    if isinstance(value, list):
        return [_serialize_value(v) for v in value]
    return value


def serialize_docs(docs: list[dict]) -> list[dict]:
    return [serialize_doc(d) for d in docs]
