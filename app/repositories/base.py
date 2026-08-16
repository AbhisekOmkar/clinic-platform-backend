from datetime import datetime
from typing import Callable

from motor.motor_asyncio import AsyncIOMotorCollection

from app.utils.serializers import serialize_doc, serialize_docs


class BaseRepository:
    """Thin async Mongo access layer.

    Collections are resolved lazily so repositories can be module-level
    singletons instantiated before the DB connects. All reads exclude
    soft-deleted documents.
    """

    def __init__(self, collection_getter: Callable[[], AsyncIOMotorCollection]):
        self._get_collection = collection_getter

    @property
    def collection(self) -> AsyncIOMotorCollection:
        return self._get_collection()

    async def find_one(self, filter: dict, projection: dict | None = None) -> dict | None:
        filter.setdefault("deleted_at", None)
        return serialize_doc(await self.collection.find_one(filter, projection))

    async def find_many(
        self,
        filter: dict,
        skip: int = 0,
        limit: int = 200,
        sort: list[tuple] | None = None,
        projection: dict | None = None,
    ) -> list[dict]:
        filter.setdefault("deleted_at", None)
        cursor = self.collection.find(filter, projection)
        if sort:
            cursor = cursor.sort(sort)
        cursor = cursor.skip(skip).limit(limit)
        return serialize_docs(await cursor.to_list(length=limit))

    async def count(self, filter: dict) -> int:
        filter.setdefault("deleted_at", None)
        return await self.collection.count_documents(filter)

    async def insert_one(self, document: dict, created_by: str = "system") -> dict:
        document.setdefault("created_at", datetime.utcnow())
        document.setdefault("created_by", created_by)
        document.setdefault("deleted_at", None)
        await self.collection.insert_one(document)
        return serialize_doc(document)

    async def update_one(self, filter: dict, update: dict, updated_by: str = "system") -> bool:
        update["updated_at"] = datetime.utcnow()
        update["updated_by"] = updated_by
        result = await self.collection.update_one(filter, {"$set": update})
        return result.modified_count > 0

    async def soft_delete(self, filter: dict, deleted_by: str = "system") -> bool:
        return await self.update_one(
            filter, {"deleted_at": datetime.utcnow(), "deleted_by": deleted_by}, deleted_by
        )

    async def hard_delete(self, filter: dict) -> int:
        result = await self.collection.delete_many(filter)
        return result.deleted_count
