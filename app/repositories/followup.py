from app.db.mongodb import Collections, get_database
from app.repositories.base import BaseRepository


class FollowupRepository(BaseRepository):
    def __init__(self):
        super().__init__(lambda: get_database()[Collections.FOLLOWUPS])

    async def list_open(self, limit: int = 100) -> list[dict]:
        return await self.find_many(
            {"status": "open"}, sort=[("created_at", -1)], limit=limit
        )

    async def list_recent(self, limit: int = 100) -> list[dict]:
        return await self.find_many({}, sort=[("created_at", -1)], limit=limit)


followup_repository = FollowupRepository()
