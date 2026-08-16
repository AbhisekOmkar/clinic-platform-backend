from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.repositories import appointment_repository
from app.services.booking_service import booking_service

router = APIRouter(prefix="/appointments", tags=["Appointments"])


class BookRequest(BaseModel):
    patient_name: str = Field(..., description="Full name; bookings are never anonymous")
    phone: str
    practitioner_id: str
    branch_id: str
    date_local: str = Field(..., description="YYYY-MM-DD in clinic timezone")
    start_hm: str = Field(..., description="HH:MM 24h clinic-local slot start")
    reason: str | None = None
    call_id: str | None = None
    patient_id: str | None = None


class RescheduleRequest(BaseModel):
    date_local: str
    start_hm: str
    practitioner_id: str | None = None
    branch_id: str | None = None
    call_id: str | None = None


class CancelRequest(BaseModel):
    reason: str | None = None
    call_id: str | None = None


@router.post("")
async def book_appointment(body: BookRequest):
    return await booking_service.book(
        patient_name=body.patient_name,
        phone=body.phone,
        practitioner_id=body.practitioner_id,
        branch_id=body.branch_id,
        date_local=body.date_local,
        start_hm=body.start_hm,
        reason=body.reason,
        call_id=body.call_id,
        patient_id=body.patient_id,
    )


@router.get("")
async def list_appointments(phone: str | None = None, patient_id: str | None = None, limit: int = 100):
    if patient_id:
        return {"appointments": await appointment_repository.list_for_patient(patient_id)}
    if phone:
        from app.utils.phone import normalize_phone

        return {"appointments": await appointment_repository.list_for_phone(normalize_phone(phone))}
    return {"appointments": await appointment_repository.list_recent(limit=limit)}


@router.get("/{appointment_id}")
async def get_appointment(appointment_id: str):
    appointment = await appointment_repository.get_by_appointment_id(appointment_id)
    if appointment is None:
        raise HTTPException(status_code=404, detail="Appointment not found")
    return appointment


@router.post("/{appointment_id}/reschedule")
async def reschedule_appointment(appointment_id: str, body: RescheduleRequest):
    return await booking_service.reschedule(
        appointment_id,
        date_local=body.date_local,
        start_hm=body.start_hm,
        practitioner_id=body.practitioner_id,
        branch_id=body.branch_id,
        call_id=body.call_id,
    )


@router.post("/{appointment_id}/cancel")
async def cancel_appointment(appointment_id: str, body: CancelRequest):
    return await booking_service.cancel(appointment_id, reason=body.reason, call_id=body.call_id)
