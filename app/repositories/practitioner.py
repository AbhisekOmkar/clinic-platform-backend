import re

from app.db.mongodb import Collections, get_database
from app.repositories.base import BaseRepository


class PractitionerRepository(BaseRepository):
    def __init__(self):
        super().__init__(lambda: get_database()[Collections.PRACTITIONERS])

    async def get_by_practitioner_id(self, practitioner_id: str) -> dict | None:
        return await self.find_one({"practitioner_id": practitioner_id})

    async def search(
        self,
        specialty: str | None = None,
        branch_id: str | None = None,
        name_contains: str | None = None,
    ) -> list[dict]:
        filter: dict = {}
        if specialty:
            filter["specialty"] = {"$regex": f"^{re.escape(specialty)}$", "$options": "i"}
        if branch_id:
            filter["weekly_schedule.branch_id"] = branch_id
        if name_contains:
            filter["full_name"] = {"$regex": re.escape(name_contains), "$options": "i"}
        return await self.find_many(filter, sort=[("full_name", 1)])

    async def list_all(self) -> list[dict]:
        return await self.find_many({}, sort=[("full_name", 1)])

    async def distinct_specialties(self) -> list[str]:
        return sorted(await self.collection.distinct("specialty", {"deleted_at": None}))


practitioner_repository = PractitionerRepository()
