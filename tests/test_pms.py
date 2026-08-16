"""Mock PMS write-back: idempotency and defined failure behaviour."""

from app.services.pms_sync_service import pms_sync_service
from app.utils import timeutil
from tests.test_availability import next_weekday

PMS_HEADERS = {"X-PMS-Key": "pms_test_key"}


def _pms_payload(appointment_id: str = "appt-1") -> dict:
    return {
        "appointment_id": appointment_id,
        "patient": {"name": "Test", "phone": "+919800000000"},
        "practitioner_id": "p1",
        "practitioner_name": "Dr. P",
        "branch_id": "b1",
        "starts_at_local": "2026-08-20T10:00:00+05:30",
        "starts_at_utc": "2026-08-20T04:30:00",
        "duration_minutes": 15,
    }


async def test_pms_requires_auth_and_idempotency_key(client, clean_bookings):
    no_auth = await client.post("/pms/api/v1/appointments", json=_pms_payload())
    assert no_auth.status_code == 401
    no_key = await client.post(
        "/pms/api/v1/appointments", json=_pms_payload(), headers=PMS_HEADERS
    )
    assert no_key.status_code == 400


async def test_pms_idempotent_replay_creates_nothing(client, clean_bookings, db):
    headers = {**PMS_HEADERS, "Idempotency-Key": "idem-abc"}
    first = await client.post("/pms/api/v1/appointments", json=_pms_payload(), headers=headers)
    second = await client.post("/pms/api/v1/appointments", json=_pms_payload(), headers=headers)
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["replayed"] is False
    assert second.json()["replayed"] is True
    assert second.json()["pms_id"] == first.json()["pms_id"]
    assert await db.pms_appointments.count_documents({}) == 1


async def test_booking_survives_pms_outage_and_recovers(client, clean_bookings, db, monkeypatch):
    """Defined failure behaviour: PMS down -> booking still confirmed with
    pms.status=pending; retry drain later syncs it exactly once."""
    from app.config.settings import settings

    monkeypatch.setattr(settings, "pms_chaos_failure_rate", 1.0)

    monday = next_weekday(timeutil.today_local(), 0)
    response = await client.post(
        "/api/v1/appointments",
        json={
            "patient_name": "Outage Person",
            "phone": "+919899999999",
            "practitioner_id": "dr-meera-shridhar",
            "branch_id": "br-indiranagar",
            "date_local": monday.isoformat(),
            "start_hm": "11:00",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "confirmed"
    assert body["pms_sync"] == "pending"

    # PMS recovers; the background drain (invoked directly here) syncs it.
    monkeypatch.setattr(settings, "pms_chaos_failure_rate", 0.0)
    await pms_sync_service._drain_pending()

    stored = await db.appointments.find_one(
        {"appointment_id": body["appointment"]["appointment_id"]}
    )
    assert stored["pms"]["status"] == "synced"
    assert stored["pms"]["pms_id"]
    assert await db.pms_appointments.count_documents(
        {"idempotency_key": body["appointment"]["appointment_id"]}
    ) == 1

    # A second drain is a no-op (idempotency): still exactly one PMS record.
    await pms_sync_service._drain_pending()
    assert await db.pms_appointments.count_documents(
        {"idempotency_key": body["appointment"]["appointment_id"]}
    ) == 1
