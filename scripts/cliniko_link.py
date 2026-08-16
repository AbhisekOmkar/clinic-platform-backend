"""Link the local clinic catalog to a real Cliniko account.

Fetches businesses (locations), practitioners and appointment types from the
Cliniko API and writes their ids onto our branch/practitioner documents:

    branch.cliniko_business_id
    practitioner.cliniko_practitioner_id
    practitioner.cliniko_appointment_type_id

Matching is by name (case/spacing tolerant; 'DR. MEERA SHRIDHAR' matches
'Meera Shridhar'). Appointment types are matched per practitioner specialty
('Dermatology consult' etc.), falling back to the first appointment type.

Run after setting CLINIKO_API_KEY in .env:
    poetry run python scripts/cliniko_link.py [--report-only]
"""

import asyncio
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

from app.config.settings import settings  # noqa: E402
from app.pms.cliniko_client import ClinikoClient  # noqa: E402


def norm(text: str) -> str:
    text = (text or "").lower()
    text = re.sub(r"\bdr\.?\b", "", text)
    return re.sub(r"[^a-z0-9]", "", text)


async def main(report_only: bool = False) -> int:
    if not ClinikoClient.configured():
        print("CLINIKO_API_KEY is not set")
        return 1

    businesses = await ClinikoClient.list_all("/businesses", "businesses")
    practitioners = await ClinikoClient.list_all("/practitioners", "practitioners")
    appointment_types = await ClinikoClient.list_all("/appointment_types", "appointment_types")
    print(f"Cliniko: {len(businesses)} businesses, {len(practitioners)} practitioners, "
          f"{len(appointment_types)} appointment types")

    client = AsyncIOMotorClient(settings.mongodb_url)
    db = client[settings.mongodb_database]

    unmatched: list[str] = []

    for branch in await db.branches.find({"deleted_at": None}).to_list(50):
        match = next(
            (b for b in businesses if norm(branch["name"]) in norm(b.get("business_name", ""))
             or norm(b.get("business_name", "")) in norm(branch["name"])
             or norm(branch["area"]) in norm(b.get("business_name", ""))),
            None,
        )
        if match:
            print(f"branch {branch['name']:35s} -> business {match['id']} ({match.get('business_name')})")
            if not report_only:
                await db.branches.update_one(
                    {"branch_id": branch["branch_id"]},
                    {"$set": {"cliniko_business_id": int(match["id"])}},
                )
        else:
            unmatched.append(f"branch: {branch['name']}")

    for practitioner in await db.practitioners.find({"deleted_at": None}).to_list(100):
        wanted = norm(practitioner["full_name"])
        match = next(
            (
                p
                for p in practitioners
                if norm(f"{p.get('first_name', '')}{p.get('last_name', '')}") == wanted
                or norm(f"{p.get('first_name', '')}{p.get('last_name', '')}") in wanted
                or wanted in norm(f"{p.get('first_name', '')}{p.get('last_name', '')}")
            ),
            None,
        )
        if not match:
            unmatched.append(f"practitioner: {practitioner['full_name']}")
            continue
        specialty = norm(practitioner["specialty"])
        appt_type = next(
            (t for t in appointment_types if specialty[:8] in norm(t.get("name", ""))),
            appointment_types[0] if appointment_types else None,
        )
        print(
            f"practitioner {practitioner['full_name']:28s} -> {match['id']} "
            f"(appt type: {appt_type.get('name') if appt_type else 'NONE'})"
        )
        if not report_only and appt_type:
            await db.practitioners.update_one(
                {"practitioner_id": practitioner["practitioner_id"]},
                {
                    "$set": {
                        "cliniko_practitioner_id": int(match["id"]),
                        "cliniko_appointment_type_id": int(appt_type["id"]),
                    }
                },
            )

    if unmatched:
        print("\nUNMATCHED (create these in Cliniko or rename):")
        for item in unmatched:
            print(f"  - {item}")
    client.close()
    await ClinikoClient.aclose()
    return 0 if not unmatched else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(report_only="--report-only" in sys.argv)))
