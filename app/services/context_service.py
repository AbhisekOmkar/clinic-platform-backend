"""Call-start context assembly.

One endpoint gives the agent everything it needs to open the call correctly:
- returning-patient recognition (all patients sharing the phone, so a family
  line asks for a name instead of guessing),
- dropped-call resume state,
- pending-callback recognition for unanswered outbound calls,
- upcoming appointments for the number.
"""

from datetime import datetime

from app.config.settings import settings
from app.repositories import (
    appointment_repository,
    call_session_repository,
    outbound_call_repository,
    patient_repository,
)
from app.utils import timeutil
from app.utils.phone import normalize_phone


class ContextService:
    async def call_context(self, phone: str | None, call_id: str | None = None) -> dict:
        phone = normalize_phone(phone)
        result: dict = {
            "phone": phone,
            "now_local": timeutil.now_local().isoformat(),
            "today_local": timeutil.today_local().isoformat(),
            "timezone": settings.clinic_timezone,
            "known_patients": [],
            "upcoming_appointments": [],
            "resumable_session": None,
            "pending_callback": None,
        }
        if not phone:
            return result

        patients = await patient_repository.get_by_phone(phone)
        result["known_patients"] = [
            {
                "patient_id": p["patient_id"],
                "full_name": p["full_name"],
                "last_visit_note": (p.get("notes") or [None])[-1],
            }
            for p in patients
        ]

        upcoming = await appointment_repository.list_for_phone(
            phone, upcoming_only_after_utc=datetime.utcnow()
        )
        result["upcoming_appointments"] = [
            {
                "appointment_id": a["appointment_id"],
                "patient_name": a["patient_name"],
                "practitioner_name": a["practitioner_name"],
                "specialty": a["specialty"],
                "branch_name": a["branch_name"],
                "date_local": a["date_local"],
                "start_hm": a["start_hm"],
                "display": a.get("display"),
            }
            for a in upcoming[:6]
        ]

        session = await call_session_repository.latest_resumable_for_phone(
            phone,
            settings.session_resume_window_minutes,
            exclude_call_id=call_id,
        )
        if session:
            result["resumable_session"] = {
                "call_id": session["call_id"],
                "status": session["status"],
                "stage": session.get("stage"),
                "language": session.get("language"),
                "collected": session.get("collected", {}),
                "summary": session.get("summary"),
                "updated_at": session.get("updated_at"),
            }

        callback = await outbound_call_repository.latest_unanswered_for_phone(
            phone, settings.callback_window_hours
        )
        if callback:
            result["pending_callback"] = {
                "outbound_id": callback["outbound_id"],
                "purpose": callback.get("purpose"),
                "context": callback.get("context", {}),
                "attempted_at": callback.get("created_at"),
            }
        return result


context_service = ContextService()
