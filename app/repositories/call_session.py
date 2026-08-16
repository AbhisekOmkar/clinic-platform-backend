from datetime import datetime, timedelta

from app.db.mongodb import Collections, get_database
from app.repositories.base import BaseRepository


class CallSessionRepository(BaseRepository):
    """Per-call conversation state, persisted every time it changes so a
    dropped call can be resumed by the next call from the same number."""

    def __init__(self):
        super().__init__(lambda: get_database()[Collections.CALL_SESSIONS])

    async def upsert(self, call_id: str, data: dict) -> None:
        data["call_id"] = call_id
        data["updated_at"] = datetime.utcnow()
        await self.collection.update_one(
            {"call_id": call_id},
            {"$set": data, "$setOnInsert": {"created_at": datetime.utcnow(), "deleted_at": None}},
            upsert=True,
        )

    async def get_by_call_id(self, call_id: str) -> dict | None:
        return await self.find_one({"call_id": call_id})

    async def latest_resumable_for_phone(
        self, phone: str, window_minutes: int, exclude_call_id: str | None = None
    ) -> dict | None:
        """Most recent session for this phone that ended (or stalled) without
        completion inside the resume window."""
        cutoff = datetime.utcnow() - timedelta(minutes=window_minutes)
        filter: dict = {
            "phone": phone,
            "status": {"$in": ["active", "dropped"]},
            "updated_at": {"$gte": cutoff},
            "deleted_at": None,
        }
        if exclude_call_id:
            filter["call_id"] = {"$ne": exclude_call_id}
        docs = await self.find_many(filter, sort=[("updated_at", -1)], limit=1)
        return docs[0] if docs else None

    async def mark_status(self, call_id: str, status: str) -> bool:
        return await self.update_one({"call_id": call_id}, {"status": status})


call_session_repository = CallSessionRepository()
