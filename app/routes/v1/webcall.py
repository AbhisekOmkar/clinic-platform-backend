"""Browser-based test calls.

The dashboard (or any LiveKit client) POSTs here, gets a token + room, and the
platform dispatches the voice agent into the room. `caller_phone` lets a
tester simulate calling from a specific number so returning-patient,
family-line, dropped-call and callback scenarios are all reproducible without
a PSTN line.
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.communication.room_manager import room_manager
from app.config.settings import settings
from app.repositories import call_repository
from app.utils.phone import normalize_phone

router = APIRouter(tags=["WebCall"])


class WebCallRequest(BaseModel):
    caller_phone: str | None = None
    caller_name: str | None = None


@router.post("/webcall")
async def create_webcall(body: WebCallRequest):
    if not settings.livekit_url or not settings.livekit_api_key:
        raise HTTPException(status_code=503, detail="LiveKit is not configured")
    call_id = str(uuid.uuid4())
    room_name = f"webcall-{call_id[:8]}"
    phone = normalize_phone(body.caller_phone) or f"web:{call_id[:8]}"

    await call_repository.insert_one(
        {
            "call_id": call_id,
            "direction": "web",
            "phone": phone,
            "room_name": room_name,
            "status": "in_progress",
            "started_at": datetime.utcnow(),
        }
    )
    await room_manager.create_room_with_agent(
        room_name,
        {
            "call_id": call_id,
            "direction": "web",
            "phone": phone,
            "caller_name": body.caller_name,
        },
    )
    token = room_manager.mint_user_token(room_name, identity=f"caller-{call_id[:8]}")
    return {
        "call_id": call_id,
        "room_name": room_name,
        "token": token,
        "livekit_url": settings.livekit_url,
    }
