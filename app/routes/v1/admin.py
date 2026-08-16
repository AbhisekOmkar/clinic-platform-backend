"""Test/eval support: reset dynamic state between eval scenarios.

Enabled only when ALLOW_TEST_RESET=true (defaults on for env=local, off in
production) so the eval harness is re-runnable from a clean clone without a
mongo client of its own.
"""

from fastapi import APIRouter, HTTPException

from app.config.settings import settings
from app.db.mongodb import get_database

router = APIRouter(prefix="/admin", tags=["Admin"])

DYNAMIC_COLLECTIONS = [
    "appointments",
    "patients",
    "calls",
    "call_sessions",
    "outbound_calls",
    "followups",
    "call_latency_metrics",
    "pms_appointments",
]


def _guard() -> None:
    allowed = settings.allow_test_reset or settings.env == "local"
    if not allowed:
        raise HTTPException(status_code=403, detail="Test reset is disabled in this environment")


@router.post("/reset")
async def reset_dynamic_state():
    _guard()
    db = get_database()
    wiped = {}
    for coll in DYNAMIC_COLLECTIONS:
        result = await db[coll].delete_many({})
        wiped[coll] = result.deleted_count
    return {"ok": True, "wiped": wiped}
