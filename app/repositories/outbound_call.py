from datetime import datetime, timedelta

from app.db.mongodb import Collections, get_database
from app.repositories.base import BaseRepository


class OutboundCallRepository(BaseRepository):
    def __init__(self):
        super().__init__(lambda: get_database()[Collections.OUTBOUND_CALLS])

    async def get_by_outbound_id(self, outbound_id: str) -> dict | None:
        return await self.find_one({"outbound_id": outbound_id})

    async def latest_unanswered_for_phone(self, phone: str, window_hours: int) -> dict | None:
        """The clinic tried this number and nobody picked up; if they call
        back within the window the agent should treat it as a callback."""
        cutoff = datetime.utcnow() - timedelta(hours=window_hours)
        docs = await self.find_many(
            {
                "phone": phone,
                "status": {"$in": ["no_answer", "voicemail", "initiated"]},
                "created_at": {"$gte": cutoff},
            },
            sort=[("created_at", -1)],
            limit=1,
        )
        return docs[0] if docs else None

    async def mark_status(self, outbound_id: str, status: str, extra: dict | None = None) -> bool:
        update = {"status": status}
        if extra:
            update.update(extra)
        return await self.update_one({"outbound_id": outbound_id}, update)


outbound_call_repository = OutboundCallRepository()
