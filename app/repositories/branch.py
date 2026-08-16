from app.db.mongodb import Collections, get_database
from app.repositories.base import BaseRepository


class BranchRepository(BaseRepository):
    def __init__(self):
        super().__init__(lambda: get_database()[Collections.BRANCHES])

    async def get_by_branch_id(self, branch_id: str) -> dict | None:
        return await self.find_one({"branch_id": branch_id})

    async def get_by_code(self, code: str) -> dict | None:
        return await self.find_one({"code": code.upper()})

    async def list_all(self) -> list[dict]:
        return await self.find_many({}, sort=[("code", 1)])


branch_repository = BranchRepository()
