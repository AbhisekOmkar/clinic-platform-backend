"""Mock PMS (Cliniko-flavoured) write-back API.

Deliberately shaped like an external practice-management system:
- its own auth header (X-PMS-Key), its own datastore collection, its own ids;
- POST /appointments honours Idempotency-Key: replaying a key returns the
  original record with replayed=true and creates nothing;
- chaos injection for failure-path testing: X-Chaos: fail forces a 503, and
  PMS_CHAOS_FAILURE_RATE injects random 503s so the backend's retry/outbox
  behaviour can be demonstrated end to end.
"""

import random
import uuid
from datetime import datetime

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app.config.settings import settings
from app.repositories import pms_appointment_repository

router = APIRouter(prefix="/pms/api/v1", tags=["Mock PMS"])


class PmsPatient(BaseModel):
    name: str
    phone: str
    patient_id: str | None = None


class PmsAppointmentCreate(BaseModel):
    appointment_id: str = Field(..., description="Caller-side appointment id")
    patient: PmsPatient
    practitioner_id: str
    practitioner_name: str
    branch_id: str
    starts_at_local: str
    starts_at_utc: str
    duration_minutes: int
    notes: str | None = None


class PmsCancel(BaseModel):
    reason: str | None = None


def _check_auth(x_pms_key: str | None) -> None:
    if x_pms_key != settings.pms_api_key:
        raise HTTPException(status_code=401, detail="Invalid PMS API key")


def _maybe_chaos(request: Request) -> None:
    if request.headers.get("X-Chaos") == "fail":
        raise HTTPException(status_code=503, detail="PMS unavailable (forced by X-Chaos header)")
    if settings.pms_chaos_failure_rate > 0 and random.random() < settings.pms_chaos_failure_rate:
        raise HTTPException(status_code=503, detail="PMS transiently unavailable (chaos)")


@router.post("/appointments")
async def create_pms_appointment(
    body: PmsAppointmentCreate,
    request: Request,
    x_pms_key: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None),
):
    _check_auth(x_pms_key)
    if not idempotency_key:
        raise HTTPException(status_code=400, detail="Idempotency-Key header is required")

    existing = await pms_appointment_repository.get_by_idempotency_key(idempotency_key)
    if existing:
        return {
            "pms_id": existing["pms_id"],
            "state": existing["state"],
            "replayed": True,
            "created_at": existing["created_at"],
        }

    _maybe_chaos(request)

    record = {
        "pms_id": f"pms_{uuid.uuid4().hex[:12]}",
        "idempotency_key": idempotency_key,
        "appointment_id": body.appointment_id,
        "state": "booked",
        "payload": body.model_dump(),
        "created_at": datetime.utcnow().isoformat(),
    }
    await pms_appointment_repository.insert_one(dict(record))
    return {"pms_id": record["pms_id"], "state": "booked", "replayed": False, "created_at": record["created_at"]}


@router.post("/appointments/{appointment_id}/cancel")
async def cancel_pms_appointment(
    appointment_id: str,
    body: PmsCancel,
    request: Request,
    x_pms_key: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None),
):
    _check_auth(x_pms_key)
    _maybe_chaos(request)
    record = await pms_appointment_repository.find_one({"appointment_id": appointment_id})
    if record is None:
        # Cancelling something the PMS never saw is a no-op ack: the source
        # system remains authoritative.
        return {"state": "not_found", "acknowledged": True}
    await pms_appointment_repository.update_one(
        {"appointment_id": appointment_id},
        {"state": "cancelled", "cancel_reason": body.reason},
    )
    return {"pms_id": record["pms_id"], "state": "cancelled", "acknowledged": True}


@router.get("/appointments")
async def list_pms_appointments(x_pms_key: str | None = Header(default=None)):
    _check_auth(x_pms_key)
    return {"appointments": await pms_appointment_repository.list_recent()}
