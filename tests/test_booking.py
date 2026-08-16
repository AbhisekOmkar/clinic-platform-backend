"""Booking lifecycle: write-time conflict enforcement, buffers, fees, races."""

import asyncio
from datetime import timedelta

from app.utils import timeutil
from tests.helpers import add_test_practitioner, cleanup_test_practitioner, slot_near_now
from tests.test_availability import next_weekday


async def _book(client, **overrides):
    monday = next_weekday(timeutil.today_local(), 0)
    payload = {
        "patient_name": "Asha Rao",
        "phone": "+919811111111",
        "practitioner_id": "dr-meera-shridhar",
        "branch_id": "br-indiranagar",
        "date_local": monday.isoformat(),
        "start_hm": "10:00",
    }
    payload.update(overrides)
    return await client.post("/api/v1/appointments", json=payload)


async def test_booking_requires_full_name(client, clean_bookings):
    response = await _book(client, patient_name="  ")
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "PATIENT_NAME_REQUIRED"


async def test_double_booking_rejected_at_write_time(client, clean_bookings):
    first = await _book(client)
    assert first.status_code == 200
    second = await _book(client, patient_name="Someone Else", phone="+919822222222")
    assert second.status_code == 409
    detail = second.json()["detail"]
    assert detail["code"] == "SLOT_TAKEN"
    assert detail["alternatives"], "409 must carry fresh alternatives"


async def test_concurrent_race_only_one_wins(client, clean_bookings, db):
    """Two simultaneous writes for the same slot: the unique index must let
    exactly one commit, regardless of what availability said earlier."""
    await add_test_practitioner(db, practitioner_id="dr-race", buffer_minutes=0)
    date_local, start_hm = slot_near_now(hours_ahead=2)
    results = await asyncio.gather(
        *[
            client.post(
                "/api/v1/appointments",
                json={
                    "patient_name": f"Racer {i}",
                    "phone": f"+9198000000{i:02d}",
                    "practitioner_id": "dr-race",
                    "branch_id": "br-indiranagar",
                    "date_local": date_local,
                    "start_hm": start_hm,
                },
            )
            for i in range(4)
        ]
    )
    statuses = sorted(r.status_code for r in results)
    assert statuses.count(200) == 1, f"exactly one booking must win, got {statuses}"
    assert statuses.count(409) == 3
    count = await db.appointments.count_documents(
        {"practitioner_id": "dr-race", "status": "confirmed"}
    )
    assert count == 1
    await cleanup_test_practitioner(db, "dr-race")


async def test_buffer_blocks_adjacent_slot(client, clean_bookings):
    """Meera has a 5-minute buffer: 10:00 booked -> 10:15 must be refused."""
    first = await _book(client)
    assert first.status_code == 200
    adjacent = await _book(client, patient_name="Next Person", phone="+919833333333", start_hm="10:15")
    assert adjacent.status_code == 409
    ok_after_gap = await _book(client, patient_name="Next Person", phone="+919833333333", start_hm="10:30")
    assert ok_after_gap.status_code == 200


async def test_outside_schedule_rejected(client, clean_bookings):
    response = await _book(client, start_hm="15:00")  # Meera Mon: 10-13 & 17-20 only
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "OUTSIDE_SCHEDULE"


async def test_off_grid_time_rejected(client, clean_bookings):
    response = await _book(client, start_hm="10:07")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "OUTSIDE_SCHEDULE"


async def test_family_line_two_patients_same_phone(client, clean_bookings, db):
    first = await _book(client, patient_name="Rohan Mehta", phone="+919844444444")
    assert first.status_code == 200
    second = await _book(
        client, patient_name="Priya Mehta", phone="+919844444444", start_hm="10:30"
    )
    assert second.status_code == 200
    patients = await db.patients.count_documents({"phone": "+919844444444"})
    assert patients == 2


async def test_reschedule_moves_and_links(client, clean_bookings):
    booked = await _book(client)
    appointment_id = booked.json()["appointment"]["appointment_id"]
    monday = next_weekday(timeutil.today_local(), 0)
    response = await client.post(
        f"/api/v1/appointments/{appointment_id}/reschedule",
        json={"date_local": monday.isoformat(), "start_hm": "17:30"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["appointment"]["start_hm"] == "17:30"
    assert body["previous_appointment_id"] == appointment_id
    old = await client.get(f"/api/v1/appointments/{appointment_id}")
    assert old.json()["status"] == "rescheduled"
    # the freed slot is bookable again
    rebook = await _book(client, patient_name="New Person", phone="+919855555555")
    assert rebook.status_code == 200


async def test_change_fee_only_inside_policy_window(client, clean_bookings, db):
    """Fee (₹250 within 4h) must apply near the appointment and NOT apply far out."""
    await add_test_practitioner(db, practitioner_id="dr-feetest")
    near_date, near_hm = slot_near_now(hours_ahead=2)
    far_date, far_hm = slot_near_now(hours_ahead=50)

    near = await client.post(
        "/api/v1/appointments",
        json={
            "patient_name": "Near Person",
            "phone": "+919866666666",
            "practitioner_id": "dr-feetest",
            "branch_id": "br-indiranagar",
            "date_local": near_date,
            "start_hm": near_hm,
        },
    )
    far = await client.post(
        "/api/v1/appointments",
        json={
            "patient_name": "Far Person",
            "phone": "+919877777777",
            "practitioner_id": "dr-feetest",
            "branch_id": "br-indiranagar",
            "date_local": far_date,
            "start_hm": far_hm,
        },
    )
    assert near.status_code == 200 and far.status_code == 200

    near_cancel = await client.post(
        f"/api/v1/appointments/{near.json()['appointment']['appointment_id']}/cancel",
        json={"reason": "test"},
    )
    far_cancel = await client.post(
        f"/api/v1/appointments/{far.json()['appointment']['appointment_id']}/cancel",
        json={"reason": "test"},
    )
    assert near_cancel.json()["change_fee"]["applies"] is True
    assert near_cancel.json()["change_fee"]["fee_inr"] == 250
    assert far_cancel.json()["change_fee"]["applies"] is False
    assert far_cancel.json()["change_fee"]["fee_inr"] == 0
    await cleanup_test_practitioner(db, "dr-feetest")


async def test_cancel_frees_slot(client, clean_bookings):
    booked = await _book(client)
    appointment_id = booked.json()["appointment"]["appointment_id"]
    response = await client.post(
        f"/api/v1/appointments/{appointment_id}/cancel", json={"reason": "cannot come"}
    )
    assert response.status_code == 200
    rebook = await _book(client, patient_name="Fresh Person", phone="+919888888888")
    assert rebook.status_code == 200
