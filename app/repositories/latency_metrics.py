from datetime import datetime

from app.db.mongodb import Collections, get_database
from app.repositories.base import BaseRepository


class LatencyMetricsRepository(BaseRepository):
    def __init__(self):
        super().__init__(lambda: get_database()[Collections.CALL_LATENCY_METRICS])

    async def upsert_for_call(self, call_id: str, metrics: dict) -> None:
        metrics["call_id"] = call_id
        metrics["updated_at"] = datetime.utcnow()
        await self.collection.update_one(
            {"call_id": call_id},
            {"$set": metrics, "$setOnInsert": {"created_at": datetime.utcnow(), "deleted_at": None}},
            upsert=True,
        )

    async def get_by_call_id(self, call_id: str) -> dict | None:
        return await self.find_one({"call_id": call_id})

    async def list_recent(self, limit: int = 200) -> list[dict]:
        return await self.find_many({}, sort=[("created_at", -1)], limit=limit)


latency_metrics_repository = LatencyMetricsRepository()
