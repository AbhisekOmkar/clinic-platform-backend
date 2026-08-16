"""Availability engine correctness."""

from datetime import timedelta
from zoneinfo import ZoneInfo

from app.utils import timeutil


def next_weekday(base, weekday: int):
    days = (weekday - base.weekday()) % 7
    if days == 0:
        days = 7  # strictly future date so past-time filtering never interferes
    return base + timedelta(days=days)


async def test_today_matches_clinic_timezone(client):
    """Guard against the UTC 'today becomes tomorrow' bug."""
    from datetime import datetime

    response = await client.get("/api/v1/availability", params={"specialty": "Dermatology"})
    body = response.json()
    ist_today = datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()
    assert body["today"] == ist_today
    assert body["timezone"] == "Asia/Kolkata"


async def test_slots_respect_schedule_blocks(client, clean_bookings):
    """DR. MEERA SHRIDHAR consults Mon 10:00-13:00 and 17:00-20:00 at
    Indiranagar; no slots may appear outside those blocks."""
    monday = next_weekday(timeutil.today_local(), 0)
    response = await client.get(
        "/api/v1/availability",
        params={
            "practitioner_id": "dr-meera-shridhar",
            "date_from": monday.isoformat(),
            "limit": 50,
        },
    )
    slots = response.json()["slots"]
    assert slots, "expected Monday slots"
    for slot in slots:
        minutes = timeutil.minutes_of_day(slot["start_hm"])
        in_morning = 10 * 60 <= minutes < 13 * 60
        in_evening = 17 * 60 <= minutes < 20 * 60
        assert in_morning or in_evening, f"slot {slot['start_hm']} outside schedule"
        assert slot["branch_id"] == "br-indiranagar"
        assert slot["duration_minutes"] == 15


async def test_weekday_preference_filter(client):
    """'Mondays and Wednesdays work for me' must only return those days."""
    response = await client.get(
        "/api/v1/availability",
        params={"specialty": "General Medicine", "weekdays": "monday,wednesday", "limit": 50},
    )
    for slot in response.json()["slots"]:
        weekday = timeutil.parse_local_date(slot["date_local"]).weekday()
        assert weekday in (0, 2)


async def test_time_window_afternoon_around_430(client):
    """'In the afternoon, around 4:30' -> after_time+near_time honoured."""
    response = await client.get(
        "/api/v1/availability",
        params={
            "specialty": "General Medicine",
            "after_time": "16:00",
            "near_time": "16:30",
            "limit": 10,
        },
    )
    slots = response.json()["slots"]
    assert slots
    for slot in slots:
        assert timeutil.minutes_of_day(slot["start_hm"]) >= 16 * 60
    # nearest-first within the first day returned
    first_day = slots[0]["date_local"]
    same_day = [s for s in slots if s["date_local"] == first_day]
    deltas = [abs(timeutil.minutes_of_day(s["start_hm"]) - (16 * 60 + 30)) for s in same_day]
    assert deltas == sorted(deltas)


async def test_earliest_scope_searches_all_branches(client, clean_bookings, db):
    """If every Indiranagar dermatology slot on a day is booked, earliest
    must surface the HSR practitioner — not claim nothing is available and
    not anchor on one practitioner's later slot."""
    from tests.helpers import add_test_practitioner

    # Fill Meera + Swagata's first bookable day by booking via the API would
    # be slow; instead pick a Wednesday where IND derm = Meera only
    # (Swagata Tue/Thu/Sat) and HSR has Tejashwini 11:00 start.
    wednesday = next_weekday(timeutil.today_local(), 2)
    # Fill Meera's Wednesday morning block (10:00-13:00). She has a 5-minute
    # buffer, so a booking every 30 minutes blocks its own slot AND the
    # adjacent one — 6 bookings make the whole block unavailable.
    for i in range(6):
        minutes = 10 * 60 + i * 30
        response = await client.post(
            "/api/v1/appointments",
            json={
                "patient_name": f"Filler {i}",
                "phone": "+919000000001",
                "practitioner_id": "dr-meera-shridhar",
                "branch_id": "br-indiranagar",
                "date_local": wednesday.isoformat(),
                "start_hm": timeutil.hm_of_minutes(minutes),
            },
        )
        assert response.status_code == 200, response.text
    response = await client.get(
        "/api/v1/availability",
        params={
            "specialty": "Dermatology",
            "date_from": wednesday.isoformat(),
            "date_to": wednesday.isoformat(),
            "scope": "earliest",
            "limit": 3,
        },
    )
    body = response.json()
    assert body["slots"], "cross-branch earliest returned nothing despite HSR availability"
    top = body["slots"][0]
    # Meera's morning is full; global earliest that day is HSR's Tejashwini 11:00
    assert top["branch_id"] == "br-hsr"
    assert top["practitioner_id"] == "dr-tejashwini-sm"
    assert top["start_hm"] == "11:00"


async def test_branch_specific_triage_is_consistent(client):
    """Named-branch specialty queries must work deterministically."""
    for _ in range(5):
        response = await client.get(
            "/api/v1/availability",
            params={"specialty": "Paediatrics", "branch_id": "br-hsr", "limit": 5},
        )
        assert response.status_code == 200
        slots = response.json()["slots"]
        assert slots
        assert all(s["branch_id"] == "br-hsr" for s in slots)
    # And a specialty absent at a branch returns empty rather than erroring
    response = await client.get(
        "/api/v1/availability",
        params={"specialty": "Paediatrics", "branch_id": "br-indiranagar"},
    )
    assert response.status_code == 200
    assert response.json()["slots"] == []


async def test_min_notice_excludes_immediate_slots(client, db, clean_bookings):
    from tests.helpers import add_test_practitioner, cleanup_test_practitioner

    await add_test_practitioner(db)
    response = await client.get(
        "/api/v1/availability",
        params={"practitioner_id": "dr-test-allday", "limit": 1},
    )
    slot = response.json()["slots"][0]
    slot_local = timeutil.combine_local(
        timeutil.parse_local_date(slot["date_local"]), slot["start_hm"]
    )
    assert slot_local >= timeutil.now_local() + timedelta(minutes=29)
    await cleanup_test_practitioner(db)
