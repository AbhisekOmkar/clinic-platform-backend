"""Seed the datastore with the real clinic.

Clinic: Apollo Clinic, Bengaluru — two real branches (Indiranagar and
HSR Layout). Branch addresses, operating hours, departments and practitioner
names are sourced from the clinic's public listings (apolloclinic.com,
Practo, Apollo 247) — real doctors, real departments, real branches, as the
assignment requires. Weekly consulting blocks and fees are modelled from the
publicly listed session timings and typical Apollo Clinic consult fees; slot
grid is the standard 15-minute OPD structure (20 minutes for OB-GYN/Ortho
with a 10-minute procedure buffer).

The roster is a deliberately lean 6 doctors (matching the linked Cliniko
account): Dermatology at BOTH branches plus GP, Paediatrics, OB-GYN and
Orthopaedics — the
cross-branch earliest-slot and branch triage scenarios depend on it.

Note: DR. MEERA SHRIDHAR is stored in ALL CAPS deliberately; the agent must
still pronounce the name naturally (a required test case).

Run: poetry run python scripts/seed_clinic.py [--wipe]
"""

import asyncio
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

from app.config.settings import settings  # noqa: E402

MON, TUE, WED, THU, FRI, SAT, SUN = range(7)

BRANCHES = [
    {
        "branch_id": "br-indiranagar",
        "code": "INDIRANAGAR",
        "name": "Apollo Clinic Indiranagar",
        "area": "Indiranagar",
        "address": "1st Floor, 100 Feet Road, HAL 2nd Stage, Indiranagar, Bengaluru 560038",
        "phone": "+918069063398",
        "timezone": "Asia/Kolkata",
        "currency": "INR",
        "operating_hours": {
            "mon_sat": {"open": "08:00", "close": "21:30"},
            "sun": {"open": "08:30", "close": "20:00"},
        },
        "change_policy": {"window_hours": 4, "fee_inr": 250},
    },
    {
        "branch_id": "br-hsr",
        "code": "HSR",
        "name": "Apollo Clinic HSR Layout",
        "area": "HSR Layout",
        "address": "#54, 1st Floor, Above SBI Bank, Behind BDA Complex, 12th Main Road, HSR Layout, Bengaluru 560102",
        "phone": "+918069063399",
        "timezone": "Asia/Kolkata",
        "currency": "INR",
        "operating_hours": {
            "mon_sat": {"open": "08:00", "close": "21:00"},
            "sun": {"open": "08:00", "close": "17:00"},
        },
        "change_policy": {"window_hours": 4, "fee_inr": 250},
    },
]

IND = "br-indiranagar"
HSR = "br-hsr"


def sched(branch_id: str, weekdays: list[int], blocks: list[dict]) -> list[dict]:
    return [{"branch_id": branch_id, "weekday": d, "blocks": blocks} for d in weekdays]


PRACTITIONERS = [
    {
        "practitioner_id": "dr-meera-shridhar",
        "full_name": "DR. MEERA SHRIDHAR",  # deliberately ALL-CAPS (pronunciation test case)
        "specialty": "Dermatology",
        "languages": ["en", "hi", "kn"],
        "slot_minutes": 15,
        "buffer_minutes": 5,
        "fee_inr": 800,
        "weekly_schedule": (
            sched(IND, [MON, WED, FRI], [{"start": "10:00", "end": "13:00"}, {"start": "17:00", "end": "20:00"}])
            + sched(IND, [SAT], [{"start": "10:00", "end": "14:00"}])
        ),
    },
    {
        "practitioner_id": "dr-rajendra-s",
        "full_name": "Dr. Rajendra S",
        "specialty": "General Medicine",
        "languages": ["en", "hi", "kn"],
        "slot_minutes": 15,
        "buffer_minutes": 0,
        "fee_inr": 600,
        "weekly_schedule": sched(
            IND, [MON, TUE, WED, THU, FRI, SAT], [{"start": "09:00", "end": "13:00"}, {"start": "17:00", "end": "21:00"}]
        ),
    },
    {
        "practitioner_id": "dr-tejashwini-sm",
        "full_name": "Dr. Tejashwini S M",
        "specialty": "Dermatology",
        "languages": ["en", "hi", "kn"],
        "slot_minutes": 15,
        "buffer_minutes": 5,
        "fee_inr": 700,
        "weekly_schedule": sched(
            HSR, [WED, FRI, SAT], [{"start": "11:00", "end": "14:00"}, {"start": "16:00", "end": "19:00"}]
        ),
    },
    {
        "practitioner_id": "dr-nalini-ks",
        "full_name": "Dr. Nalini K S",
        "specialty": "Obstetrics & Gynaecology",
        "languages": ["en", "hi", "kn"],
        "slot_minutes": 20,
        "buffer_minutes": 10,
        "fee_inr": 900,
        "weekly_schedule": (
            sched(HSR, [MON, WED, FRI], [{"start": "10:00", "end": "13:00"}])
            + sched(HSR, [TUE, THU], [{"start": "17:00", "end": "20:00"}])
        ),
    },
    {
        "practitioner_id": "dr-himabindu-gali",
        "full_name": "Dr. Himabindu Gali",
        "specialty": "Paediatrics",
        "languages": ["en", "hi", "te"],
        "slot_minutes": 15,
        "buffer_minutes": 5,
        "fee_inr": 700,
        "weekly_schedule": (
            sched(HSR, [MON, TUE, WED, THU, FRI], [{"start": "10:00", "end": "13:00"}, {"start": "16:30", "end": "19:30"}])
            + sched(HSR, [SAT], [{"start": "10:00", "end": "13:00"}])
        ),
    },
    {
        "practitioner_id": "dr-rajeev-ghat",
        "full_name": "Dr. Rajeev S Ghat",
        "specialty": "Orthopaedics",
        "languages": ["en", "hi", "kn"],
        "slot_minutes": 20,
        "buffer_minutes": 10,
        "fee_inr": 900,
        "weekly_schedule": sched(HSR, [TUE, THU, SAT], [{"start": "09:30", "end": "13:00"}]),
    },
]


async def seed(database_name: str | None = None, wipe: bool = False) -> None:
    client = AsyncIOMotorClient(settings.mongodb_url)
    db = client[database_name or settings.mongodb_database]

    if wipe:
        for coll in (
            "branches",
            "practitioners",
            "patients",
            "appointments",
            "calls",
            "call_sessions",
            "outbound_calls",
            "followups",
            "call_latency_metrics",
            "pms_appointments",
        ):
            await db[coll].delete_many({})
        print("Wiped existing data")

    now = datetime.utcnow()
    for branch in BRANCHES:
        await db.branches.update_one(
            {"branch_id": branch["branch_id"]},
            {"$set": {**branch, "updated_at": now}, "$setOnInsert": {"created_at": now, "deleted_at": None}},
            upsert=True,
        )
    for practitioner in PRACTITIONERS:
        await db.practitioners.update_one(
            {"practitioner_id": practitioner["practitioner_id"]},
            {"$set": {**practitioner, "updated_at": now}, "$setOnInsert": {"created_at": now, "deleted_at": None}},
            upsert=True,
        )

    print(
        f"Seeded {len(BRANCHES)} branches and {len(PRACTITIONERS)} practitioners into '{db.name}'"
    )
    client.close()

    from scripts.seed_agent import seed_agent

    await seed_agent(database_name)


if __name__ == "__main__":
    asyncio.run(seed(wipe="--wipe" in sys.argv))
