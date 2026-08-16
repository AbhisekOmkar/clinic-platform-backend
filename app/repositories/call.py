from app.db.mongodb import Collections, get_database
from app.repositories.base import BaseRepository


class CallRepository(BaseRepository):
    def __init__(self):
        super().__init__(lambda: get_database()[Collections.CALLS])

    async def get_by_call_id(self, call_id: str) -> dict | None:
        return await self.find_one({"call_id": call_id})

    async def list_recent(self, limit: int = 100, phone: str | None = None) -> list[dict]:
        filter: dict = {}
        if phone:
            filter["phone"] = phone
        return await self.find_many(filter, sort=[("created_at", -1)], limit=limit)


call_repository = CallRepository()
