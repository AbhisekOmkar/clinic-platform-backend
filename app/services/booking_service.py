"""Appointment lifecycle: book, reschedule, cancel.

Write-time double-booking protection is layered:
1. a partial unique index on (practitioner_id, start_utc) for confirmed
   appointments — two racing writes for the same slot cannot both commit;
2. a fresh overlap/buffer check immediately before insert;
3. a post-insert buffer verification that rolls our own booking back if a
   concurrent neighbour slipped inside the buffer window between 2 and 1.

After a booking commits locally it is written back to the PMS with the
appointment_id as the idempotency key; on PMS failure the booking stands,
sync state moves to 'pending' and a background retry loop drains it.
"""

import uuid
from datetime import datetime, timedelta

from fastapi import HTTPException
from loguru import logger

from app.repositories import (
    appointment_repository,
    branch_repository,
    patient_repository,
    practitioner_repository,
)
from app.repositories.appointment import SlotTakenError
from app.services.availability_service import availability_service
from app.services.pms_sync_service import pms_sync_service
from app.utils import timeutil
from app.utils.phone import normalize_phone


class BookingService:
    async def book(
        self,
        *,
        patient_name: str,
        phone: str,
        practitioner_id: str,
        branch_id: str,
        date_local: str,
        start_hm: str,
        reason: str | None = None,
        call_id: str | None = None,
        patient_id: str | None = None,
    ) -> dict:
        patient_name = (patient_name or "").strip()
        if not patient_name:
            # Bookings must never go through anonymously, even for a
            # recognised phone number.
            raise HTTPException(
                status_code=422,
                detail={"code": "PATIENT_NAME_REQUIRED", "message": "Full patient name is required before booking."},
            )
        phone = normalize_phone(phone)
        if not phone:
            raise HTTPException(
                status_code=422,
                detail={"code": "PHONE_REQUIRED", "message": "Caller phone number is required."},
            )

        practitioner = await practitioner_repository.get_by_practitioner_id(practitioner_id)
        if practitioner is None:
            raise HTTPException(status_code=404, detail={"code": "PRACTITIONER_NOT_FOUND", "message": "Unknown practitioner."})
        branch = await branch_repository.get_by_branch_id(branch_id)
        if branch is None:
            raise HTTPException(status_code=404, detail={"code": "BRANCH_NOT_FOUND", "message": "Unknown branch."})

        slot = self._validate_slot_on_grid(practitioner, branch, date_local, start_hm)

        patient = await self._resolve_patient(patient_id, phone, patient_name)

        appointment_id = str(uuid.uuid4())
        appointment = {
            "appointment_id": appointment_id,
            "patient_id": patient["patient_id"],
            "patient_name": patient["full_name"],
            "phone": phone,
            "practitioner_id": practitioner_id,
            "practitioner_name": practitioner["full_name"],
            "specialty": practitioner["specialty"],
            "branch_id": branch_id,
            "branch_name": branch["name"],
            "date_local": date_local,
            "start_hm": start_hm,
            "end_hm": slot["end_hm"],
            "start_local": slot["start_local"].isoformat(),
            "start_utc": slot["start_utc"],
            "end_utc": slot["end_utc"],
            "duration_minutes": practitioner.get("slot_minutes", 15),
            "fee_inr": practitioner.get("fee_inr", 0),
            "currency": branch.get("currency", "INR"),
            "reason": reason,
            "status": "confirmed",
            "booked_via_call_id": call_id,
            "display": timeutil.format_slot_display(slot["start_local"].date(), start_hm),
            "pms": {"status": "pending", "attempts": 0, "pms_id": None, "last_error": None},
        }

        await self._ensure_slot_free(practitioner, slot, exclude_appointment_id=None)
        try:
            await appointment_repository.insert_booking(appointment)
        except SlotTakenError:
            alternatives = await self._alternatives(practitioner_id, branch_id, date_local)
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "SLOT_TAKEN",
                    "message": "That slot was just taken. Availability has changed — offer a nearby alternative.",
                    "alternatives": alternatives,
                },
            ) from None

        rollback_reason = await self._post_insert_buffer_check(practitioner, appointment)
        if rollback_reason:
            await appointment_repository.hard_delete({"appointment_id": appointment_id})
            alternatives = await self._alternatives(practitioner_id, branch_id, date_local)
            raise HTTPException(
                status_code=409,
                detail={"code": "SLOT_TAKEN", "message": rollback_reason, "alternatives": alternatives},
            )

        pms_state = await pms_sync_service.sync_booking(appointment)
        appointment["pms"] = pms_state

        logger.info(
            "Booked appointment",
            appointment_id=appointment_id,
            practitioner=practitioner["full_name"],
            slot=f"{date_local} {start_hm}",
            pms_status=pms_state["status"],
        )
        return self._confirmation_payload(appointment, branch)

    async def reschedule(
        self,
        appointment_id: str,
        *,
        date_local: str,
        start_hm: str,
        practitioner_id: str | None = None,
        branch_id: str | None = None,
        call_id: str | None = None,
    ) -> dict:
        existing = await appointment_repository.get_by_appointment_id(appointment_id)
        if existing is None or existing["status"] != "confirmed":
            raise HTTPException(
                status_code=404,
                detail={"code": "APPOINTMENT_NOT_FOUND", "message": "No active appointment with that id."},
            )

        fee = await self._change_fee(existing)

        new_booking = await self.book(
            patient_name=existing["patient_name"],
            phone=existing["phone"],
            practitioner_id=practitioner_id or existing["practitioner_id"],
            branch_id=branch_id or existing["branch_id"],
            date_local=date_local,
            start_hm=start_hm,
            reason=existing.get("reason"),
            call_id=call_id,
            patient_id=existing["patient_id"],
        )
        # Only after the new slot is safely committed do we release the old one.
        await appointment_repository.set_status(
            appointment_id,
            "rescheduled",
            {"rescheduled_to": new_booking["appointment"]["appointment_id"]},
        )
        await pms_sync_service.sync_cancellation(existing, reason="rescheduled")

        new_booking["change_fee"] = fee
        new_booking["previous_appointment_id"] = appointment_id
        return new_booking

    async def cancel(self, appointment_id: str, *, reason: str | None = None, call_id: str | None = None) -> dict:
        existing = await appointment_repository.get_by_appointment_id(appointment_id)
        if existing is None or existing["status"] != "confirmed":
            raise HTTPException(
                status_code=404,
                detail={"code": "APPOINTMENT_NOT_FOUND", "message": "No active appointment with that id."},
            )
        fee = await self._change_fee(existing)
        await appointment_repository.set_status(
            appointment_id, "cancelled", {"cancel_reason": reason, "cancelled_via_call_id": call_id}
        )
        await pms_sync_service.sync_cancellation(existing, reason=reason or "cancelled")
        return {
            "status": "cancelled",
            "appointment_id": appointment_id,
            "was": {
                "practitioner_name": existing["practitioner_name"],
                "branch_name": existing["branch_name"],
                "display": existing.get("display"),
            },
            "change_fee": fee,
        }

    async def _change_fee(self, appointment: dict) -> dict:
        """Cancellation/reschedule fee applies only inside the policy window
        before the appointment start — it must not be quoted by default."""
        branch = await branch_repository.get_by_branch_id(appointment["branch_id"])
        policy = (branch or {}).get("change_policy", {})
        window_hours = policy.get("window_hours", 4)
        fee_inr = policy.get("fee_inr", 0)
        start_utc = datetime.fromisoformat(appointment["start_utc"])
        hours_to_start = (start_utc - datetime.utcnow()).total_seconds() / 3600
        applies = 0 <= hours_to_start < window_hours and fee_inr > 0
        return {
            "applies": applies,
            "fee_inr": fee_inr if applies else 0,
            "policy_window_hours": window_hours,
            "hours_to_appointment": round(hours_to_start, 1),
        }

    def _validate_slot_on_grid(
        self, practitioner: dict, branch: dict, date_local: str, start_hm: str
    ) -> dict:
        """The requested time must be a real grid slot inside the
        practitioner's schedule at that branch, in the future."""
        try:
            day = timeutil.parse_local_date(date_local)
            start_min = timeutil.minutes_of_day(start_hm)
        except (ValueError, IndexError):
            raise HTTPException(
                status_code=422,
                detail={"code": "BAD_SLOT", "message": "date must be YYYY-MM-DD and time HH:MM (24h)."},
            ) from None
        slot_minutes = practitioner.get("slot_minutes", 15)

        in_schedule = False
        for entry in practitioner.get("weekly_schedule", []):
            if entry["weekday"] != day.weekday() or entry["branch_id"] != branch["branch_id"]:
                continue
            for block in entry.get("blocks", []):
                block_start = timeutil.minutes_of_day(block["start"])
                block_end = timeutil.minutes_of_day(block["end"])
                if (
                    start_min >= block_start
                    and start_min + slot_minutes <= block_end
                    and (start_min - block_start) % slot_minutes == 0
                ):
                    in_schedule = True
                    break
        if not in_schedule:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "OUTSIDE_SCHEDULE",
                    "message": f"{practitioner['full_name']} does not consult at "
                    f"{branch['name']} at {start_hm} on {date_local}. Re-check availability.",
                },
            )

        start_local = timeutil.combine_local(day, start_hm)
        if start_local < timeutil.now_local():
            raise HTTPException(
                status_code=409,
                detail={"code": "SLOT_IN_PAST", "message": "That time is already in the past in clinic local time."},
            )
        start_utc = timeutil.local_to_utc(start_local)
        return {
            "start_local": start_local,
            "start_utc": start_utc,
            "end_utc": start_utc + timedelta(minutes=slot_minutes),
            "end_hm": timeutil.hm_of_minutes(start_min + slot_minutes),
        }

    async def _ensure_slot_free(
        self, practitioner: dict, slot: dict, exclude_appointment_id: str | None
    ) -> None:
        buffer_minutes = practitioner.get("buffer_minutes", 0)
        pad = timedelta(minutes=buffer_minutes)
        overlapping = await appointment_repository.find_active_overlapping(
            practitioner["practitioner_id"],
            slot["start_utc"] - pad,
            slot["end_utc"] + pad,
            exclude_appointment_id=exclude_appointment_id,
        )
        if overlapping:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "SLOT_TAKEN",
                    "message": "That slot conflicts with an existing booking (including required buffer).",
                    "alternatives": await self._alternatives(
                        practitioner["practitioner_id"], None, slot["start_local"].date().isoformat()
                    ),
                },
            )

    async def _post_insert_buffer_check(self, practitioner: dict, appointment: dict) -> str | None:
        buffer_minutes = practitioner.get("buffer_minutes", 0)
        if buffer_minutes <= 0:
            return None
        pad = timedelta(minutes=buffer_minutes)
        neighbours = await appointment_repository.find_active_overlapping(
            appointment["practitioner_id"],
            appointment["start_utc"] - pad,
            appointment["end_utc"] + pad,
            exclude_appointment_id=appointment["appointment_id"],
        )
        for other in neighbours:
            if other["created_at"] <= appointment["created_at"].isoformat():
                return (
                    "A concurrent booking landed inside the buffer window for this slot; "
                    "availability has changed."
                )
        return None

    async def _resolve_patient(self, patient_id: str | None, phone: str, patient_name: str) -> dict:
        if patient_id:
            patient = await patient_repository.get_by_patient_id(patient_id)
            if patient:
                return patient
        existing = await patient_repository.find_by_phone_and_name(phone, patient_name)
        if existing:
            return existing
        patient = {
            "patient_id": str(uuid.uuid4()),
            "full_name": patient_name,
            "phone": phone,
            "notes": [],
        }
        return await patient_repository.insert_one(patient)

    async def _alternatives(
        self, practitioner_id: str | None, branch_id: str | None, date_local: str
    ) -> list[dict]:
        result = await availability_service.search(
            date_from=date_local,
            date_to=date_local,
            practitioner_id=practitioner_id,
            branch_id=branch_id,
            limit=3,
        )
        return result["slots"]

    def _confirmation_payload(self, appointment: dict, branch: dict) -> dict:
        appt = dict(appointment)
        appt.pop("_id", None)
        appt["start_utc"] = appt["start_utc"].isoformat()
        appt["end_utc"] = appt["end_utc"].isoformat()
        appt.pop("created_at", None)
        appt.pop("deleted_at", None)
        return {
            "status": "confirmed",
            "appointment": appt,
            "speak_back": {
                "patient_name": appointment["patient_name"],
                "practitioner_name": appointment["practitioner_name"],
                "branch_name": branch["name"],
                "branch_area": branch.get("area"),
                "when": appointment["display"],
                "fee": f"₹{appointment['fee_inr']}",
            },
            "pms_sync": appointment["pms"]["status"],
        }


booking_service = BookingService()
