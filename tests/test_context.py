"""Call-context assembly: returning patients, dropped-call resume, callbacks."""

from app.utils import timeutil
from tests.test_availability import next_weekday

PHONE = "+919812345678"


async def test_unknown_number_returns_clean_context(client, clean_bookings):
    response = await client.get("/api/v1/agent/call-context", params={"phone": "+919999999999"})
    body = response.json()
    assert body["known_patients"] == []
    assert body["resumable_session"] is None
    assert body["pending_callback"] is None


async def test_returning_patient_and_family_line(client, clean_bookings):
    monday = next_weekday(timeutil.today_local(), 0)
    for name, hm in (("Rakesh Gupta", "10:00"), ("Meena Gupta", "10:30")):
        response = await client.post(
            "/api/v1/appointments",
            json={
                "patient_name": name,
                "phone": PHONE,
                "practitioner_id": "dr-meera-shridhar",
                "branch_id": "br-indiranagar",
                "date_local": monday.isoformat(),
                "start_hm": hm,
            },
        )
        assert response.status_code == 200
    context = (await client.get("/api/v1/agent/call-context", params={"phone": PHONE})).json()
    names = {p["full_name"] for p in context["known_patients"]}
    assert names == {"Rakesh Gupta", "Meena Gupta"}  # agent must ask WHICH one
    assert len(context["upcoming_appointments"]) == 2


async def test_dropped_call_resume_flow(client, clean_bookings):
    call_id = "call-dropped-1"
    await client.post("/api/v1/calls", json={"call_id": call_id, "direction": "inbound", "phone": PHONE})
    await client.put(
        f"/api/v1/call-sessions/{call_id}",
        json={
            "phone": PHONE,
            "status": "active",
            "stage": "choosing_slot",
            "language": "hi",
            "collected": {
                "patient_name": "Rakesh Gupta",
                "specialty": "Dermatology",
                "branch_id": "br-indiranagar",
                "candidate_slot": {"date_local": "2026-08-19", "start_hm": "17:30"},
            },
            "summary": "Wants dermatology Wednesday evening; was about to confirm 5:30pm.",
        },
    )
    # Call ends WITHOUT completion -> session must become resumable
    await client.post(
        f"/api/v1/calls/{call_id}/end",
        json={"disposition": "disconnected", "completed": False},
    )
    context = (
        await client.get(
            "/api/v1/agent/call-context", params={"phone": PHONE, "call_id": "call-new-2"}
        )
    ).json()
    resumable = context["resumable_session"]
    assert resumable is not None
    assert resumable["status"] == "dropped"
    assert resumable["stage"] == "choosing_slot"
    assert resumable["collected"]["candidate_slot"]["start_hm"] == "17:30"

    # A COMPLETED call must not resurface as resumable
    call_id2 = "call-completed-9"
    await client.post("/api/v1/calls", json={"call_id": call_id2, "direction": "inbound", "phone": "+919777777777"})
    await client.put(
        f"/api/v1/call-sessions/{call_id2}",
        json={"phone": "+919777777777", "status": "active", "stage": "done", "collected": {}},
    )
    await client.post(f"/api/v1/calls/{call_id2}/end", json={"completed": True})
    context2 = (
        await client.get("/api/v1/agent/call-context", params={"phone": "+919777777777"})
    ).json()
    assert context2["resumable_session"] is None


async def test_missed_outbound_callback_recognition(client, clean_bookings):
    response = await client.post(
        "/api/v1/outbound-calls",
        json={
            "phone": PHONE,
            "purpose": "Confirm tomorrow's dermatology appointment",
            "context": {"appointment_hint": "Dr. Meera, Wed 5:30 PM", "language": "en"},
            "status": "no_answer",
        },
    )
    assert response.status_code == 200
    context = (await client.get("/api/v1/agent/call-context", params={"phone": PHONE})).json()
    callback = context["pending_callback"]
    assert callback is not None
    assert "dermatology" in callback["purpose"].lower()

    # Once completed, it stops being a pending callback
    await client.patch(
        f"/api/v1/outbound-calls/{response.json()['outbound_id']}", json={"status": "completed"}
    )
    context = (await client.get("/api/v1/agent/call-context", params={"phone": PHONE})).json()
    assert context["pending_callback"] is None


async def test_followup_logging(client, clean_bookings):
    response = await client.post(
        "/api/v1/followups",
        json={
            "call_id": "c1",
            "phone": PHONE,
            "patient_name": "Rakesh Gupta",
            "category": "human_request",
            "details": "Caller insisted on speaking to a person about a billing dispute.",
        },
    )
    assert response.status_code == 200
    followups = (await client.get("/api/v1/followups", params={"status": "open"})).json()["followups"]
    assert any(f["category"] == "human_request" for f in followups)
