"""Call records + transcripts + latency metrics (worker-written, dashboard-read)."""

from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.repositories import (
    call_repository,
    call_session_repository,
    latency_metrics_repository,
    patient_repository,
)
from app.utils.phone import normalize_phone

router = APIRouter(tags=["Calls"])


class CallCreate(BaseModel):
    call_id: str
    direction: str = Field(default="inbound", pattern="^(inbound|outbound|web)$")
    phone: str | None = None
    room_name: str | None = None
    agent_name: str | None = None


@router.post("/calls")
async def create_call(body: CallCreate):
    existing = await call_repository.get_by_call_id(body.call_id)
    if existing:
        return existing
    record = body.model_dump()
    record["phone"] = normalize_phone(record.get("phone"))
    record["status"] = "in_progress"
    record["started_at"] = datetime.utcnow()
    return await call_repository.insert_one(record)


class CallEnd(BaseModel):
    disposition: str | None = None
    transcript: list[dict] = Field(default_factory=list)
    summary: str | None = None
    completed: bool = False
    duration_seconds: float | None = None


@router.post("/calls/{call_id}/end")
async def end_call(call_id: str, body: CallEnd):
    call = await call_repository.get_by_call_id(call_id)
    if call is None:
        raise HTTPException(status_code=404, detail="Call not found")
    await call_repository.update_one(
        {"call_id": call_id},
        {
            "status": "completed",
            "ended_at": datetime.utcnow(),
            "disposition": body.disposition,
            "transcript": body.transcript,
            "summary": body.summary,
            "duration_seconds": body.duration_seconds,
        },
    )
    # A call that ended without reaching completion stays resumable: mark the
    # session dropped so the next call from this number resumes it.
    session = await call_session_repository.get_by_call_id(call_id)
    if session and session.get("status") == "active":
        await call_session_repository.mark_status(
            call_id, "completed" if body.completed else "dropped"
        )
    return {"ok": True}


@router.get("/calls")
async def list_calls(limit: int = 100, phone: str | None = None):
    return {"calls": await call_repository.list_recent(limit=limit, phone=normalize_phone(phone))}


@router.get("/calls/{call_id}")
async def get_call(call_id: str):
    call = await call_repository.get_by_call_id(call_id)
    if call is None:
        raise HTTPException(status_code=404, detail="Call not found")
    session = await call_session_repository.get_by_call_id(call_id)
    metrics = await latency_metrics_repository.get_by_call_id(call_id)
    return {"call": call, "session": session, "latency_metrics": metrics}


class LatencyMetricsUpdate(BaseModel):
    turns: list[dict] = Field(default_factory=list)
    aggregates: dict = Field(default_factory=dict)
    providers: dict = Field(default_factory=dict)


@router.put("/call-latency-metrics/{call_id}")
async def upsert_latency_metrics(call_id: str, body: LatencyMetricsUpdate):
    await latency_metrics_repository.upsert_for_call(call_id, body.model_dump())
    return {"ok": True}


@router.get("/call-latency-metrics")
async def list_latency_metrics(limit: int = 200):
    return {"metrics": await latency_metrics_repository.list_recent(limit=limit)}


@router.get("/patients")
async def list_patients(phone: str | None = None):
    if phone:
        return {"patients": await patient_repository.get_by_phone(normalize_phone(phone))}
    return {"patients": await patient_repository.find_many({}, sort=[("created_at", -1)], limit=200)}
