from app.db.mongodb import Collections, get_database
from app.repositories.base import BaseRepository


class PatientRepository(BaseRepository):
    def __init__(self):
        super().__init__(lambda: get_database()[Collections.PATIENTS])

    async def get_by_patient_id(self, patient_id: str) -> dict | None:
        return await self.find_one({"patient_id": patient_id})

    async def get_by_phone(self, phone: str) -> list[dict]:
        """A phone number can be shared by a family; always returns a list."""
        return await self.find_many({"phone": phone}, sort=[("created_at", 1)])

    async def find_by_phone_and_name(self, phone: str, full_name: str) -> dict | None:
        import re

        return await self.find_one(
            {"phone": phone, "full_name": {"$regex": f"^{re.escape(full_name.strip())}$", "$options": "i"}}
        )


patient_repository = PatientRepository()
