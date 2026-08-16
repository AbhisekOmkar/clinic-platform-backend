"""Create MongoDB indexes, including the write-time double-booking guard.

Run: poetry run python scripts/create_indexes.py
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

from app.config.settings import settings  # noqa: E402


async def create_indexes(database_name: str | None = None) -> None:
    client = AsyncIOMotorClient(settings.mongodb_url)
    db = client[database_name or settings.mongodb_database]

    # THE double-booking guard: only one confirmed appointment may exist per
    # practitioner per exact start instant. Bookings only happen on the
    # practitioner's fixed slot grid, so start_utc equality is the conflict.
    await db.appointments.create_index(
        [("practitioner_id", 1), ("start_utc", 1)],
        unique=True,
        partialFilterExpression={"status": "confirmed"},
        name="uniq_confirmed_practitioner_slot",
    )
    await db.appointments.create_index([("appointment_id", 1)], unique=True)
    await db.appointments.create_index([("phone", 1), ("status", 1), ("start_utc", 1)])
    await db.appointments.create_index([("pms.status", 1)])

    await db.agents.create_index([("agent_id", 1)], unique=True)
    await db.agents.create_index([("status", 1)])

    await db.branches.create_index([("branch_id", 1)], unique=True)
    await db.branches.create_index([("code", 1)], unique=True)
    await db.practitioners.create_index([("practitioner_id", 1)], unique=True)
    await db.practitioners.create_index([("specialty", 1)])

    await db.patients.create_index([("patient_id", 1)], unique=True)
    await db.patients.create_index([("phone", 1)])  # NOT unique: family lines share numbers

    await db.calls.create_index([("call_id", 1)], unique=True)
    await db.calls.create_index([("phone", 1), ("created_at", -1)])
    await db.call_sessions.create_index([("call_id", 1)], unique=True)
    await db.call_sessions.create_index([("phone", 1), ("status", 1), ("updated_at", -1)])
    await db.outbound_calls.create_index([("phone", 1), ("status", 1), ("created_at", -1)])
    await db.followups.create_index([("status", 1), ("created_at", -1)])
    await db.call_latency_metrics.create_index([("call_id", 1)], unique=True)

    await db.pms_appointments.create_index([("idempotency_key", 1)], unique=True)
    await db.pms_appointments.create_index([("appointment_id", 1)])

    print(f"Indexes created on '{db.name}'")
    client.close()


if __name__ == "__main__":
    asyncio.run(create_indexes())
