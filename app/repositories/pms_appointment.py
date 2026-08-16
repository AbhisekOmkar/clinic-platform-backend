from app.db.mongodb import Collections, get_database
from app.repositories.base import BaseRepository


class PmsAppointmentRepository(BaseRepository):
    """Datastore for the mock PMS itself (kept separate from the clinic's
    operational appointments collection on purpose: the PMS is the 'external'
    system of record the backend writes back to)."""

    def __init__(self):
        super().__init__(lambda: get_database()[Collections.PMS_APPOINTMENTS])

    async def get_by_idempotency_key(self, key: str) -> dict | None:
        return await self.find_one({"idempotency_key": key})

    async def get_by_pms_id(self, pms_id: str) -> dict | None:
        return await self.find_one({"pms_id": pms_id})

    async def list_recent(self, limit: int = 200) -> list[dict]:
        return await self.find_many({}, sort=[("created_at", -1)], limit=limit)


pms_appointment_repository = PmsAppointmentRepository()
