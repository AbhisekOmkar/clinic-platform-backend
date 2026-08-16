"""Endpoints consumed by the voice worker (call context, session state,
followups, outbound-call records)."""

import uuid
from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.repositories import (
    call_session_repository,
    followup_repository,
    outbound_call_repository,
)
from app.services.context_service import context_service
from app.utils.phone import normalize_phone

router = APIRouter(tags=["Agent"])


@router.get("/agent/call-context")
async def get_call_context(phone: str | None = None, call_id: str | None = None):
    return await context_service.call_context(phone, call_id)


class SessionStateUpdate(BaseModel):
    phone: str | None = None
    status: str = Field(default="active", pattern="^(active|completed|dropped)$")
    stage: str | None = None
    language: str | None = None
    collected: dict = Field(default_factory=dict)
    summary: str | None = None


@router.put("/call-sessions/{call_id}")
async def upsert_call_session(call_id: str, body: SessionStateUpdate):
    data = body.model_dump()
    data["phone"] = normalize_phone(data.get("phone"))
    await call_session_repository.upsert(call_id, data)
    return {"ok": True, "call_id": call_id}


@router.get("/call-sessions/{call_id}")
async def get_call_session(call_id: str):
    session = await call_session_repository.get_by_call_id(call_id)
    return session or {}


class FollowupCreate(BaseModel):
    call_id: str | None = None
    phone: str | None = None
    patient_name: str | None = None
    category: str = Field(
        default="other",
        pattern="^(human_request|clinical_concern|billing|other)$",
    )
    details: str
    language: str | None = None


@router.post("/followups")
async def create_followup(body: FollowupCreate):
    followup = body.model_dump()
    followup["followup_id"] = str(uuid.uuid4())
    followup["phone"] = normalize_phone(followup.get("phone"))
    followup["status"] = "open"
    await followup_repository.insert_one(followup)
    return {
        "followup_id": followup["followup_id"],
        "status": "open",
        "message": "Logged. A staff member will call back — do not promise a live transfer.",
    }


@router.get("/followups")
async def list_followups(status: str | None = None, limit: int = 100):
    if status == "open":
        return {"followups": await followup_repository.list_open(limit=limit)}
    return {"followups": await followup_repository.list_recent(limit=limit)}


class OutboundCallCreate(BaseModel):
    phone: str
    purpose: str
    context: dict = Field(default_factory=dict)
    status: str = Field(default="no_answer", pattern="^(initiated|no_answer|voicemail|completed)$")


@router.post("/outbound-calls")
async def record_outbound_call(body: OutboundCallCreate):
    """Records an outbound attempt (used by the dashboard 'simulate missed
    call' control and by real outbound dials) so a patient calling back is
    recognised as a callback."""
    record = body.model_dump()
    record["outbound_id"] = str(uuid.uuid4())
    record["phone"] = normalize_phone(record["phone"])
    record["created_at"] = datetime.utcnow()
    await outbound_call_repository.insert_one(record)
    return {"outbound_id": record["outbound_id"], "status": record["status"]}


class OutboundStatusUpdate(BaseModel):
    status: str = Field(pattern="^(initiated|no_answer|voicemail|completed)$")


@router.patch("/outbound-calls/{outbound_id}")
async def update_outbound_call(outbound_id: str, body: OutboundStatusUpdate):
    await outbound_call_repository.mark_status(outbound_id, body.status)
    return {"ok": True}
