"""Shared test fixtures/helpers: a synthetic always-open practitioner so
tests can book relative to 'now' deterministically."""

from datetime import datetime, timedelta

from app.utils import timeutil


async def add_test_practitioner(
    db,
    practitioner_id: str = "dr-test-allday",
    branch_id: str = "br-indiranagar",
    slot_minutes: int = 15,
    buffer_minutes: int = 0,
):
    doc = {
        "practitioner_id": practitioner_id,
        "full_name": "Dr. Test Allday",
        "specialty": "Test Medicine",
        "languages": ["en", "hi"],
        "slot_minutes": slot_minutes,
        "buffer_minutes": buffer_minutes,
        "fee_inr": 500,
        "weekly_schedule": [
            {"branch_id": branch_id, "weekday": d, "blocks": [{"start": "00:00", "end": "23:45"}]}
            for d in range(7)
        ],
        "created_at": datetime.utcnow(),
        "deleted_at": None,
    }
    await db.practitioners.update_one(
        {"practitioner_id": practitioner_id}, {"$set": doc}, upsert=True
    )
    return doc


def slot_near_now(hours_ahead: float, slot_minutes: int = 15) -> tuple[str, str]:
    """A grid-aligned (date_local, start_hm) roughly hours_ahead from now."""
    target = timeutil.now_local() + timedelta(hours=hours_ahead)
    minutes = (target.hour * 60 + target.minute) // slot_minutes * slot_minutes
    day = target.date()
    if minutes >= 24 * 60:
        minutes = 0
        day = day + timedelta(days=1)
    return day.isoformat(), timeutil.hm_of_minutes(minutes)


async def cleanup_test_practitioner(db, practitioner_id: str = "dr-test-allday"):
    await db.practitioners.delete_many({"practitioner_id": practitioner_id})
