from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from loguru import logger

from app.config.settings import settings


class MongoDB:
    client: AsyncIOMotorClient = None
    db: AsyncIOMotorDatabase = None


mongodb = MongoDB()


async def connect_to_mongo(database_name: str | None = None) -> None:
    mongodb.client = AsyncIOMotorClient(
        settings.mongodb_url,
        maxPoolSize=100,
        minPoolSize=5,
        serverSelectionTimeoutMS=10_000,
        connectTimeoutMS=10_000,
    )
    mongodb.db = mongodb.client[database_name or settings.mongodb_database]
    await mongodb.client.admin.command("ping")
    logger.info(f"Connected to MongoDB database '{mongodb.db.name}'")


async def close_mongo_connection() -> None:
    if mongodb.client is not None:
        mongodb.client.close()
        mongodb.client = None
        mongodb.db = None


def get_database() -> AsyncIOMotorDatabase:
    if mongodb.db is None:
        raise RuntimeError("MongoDB is not connected. Call connect_to_mongo() first.")
    return mongodb.db


class Collections:
    AGENTS = "agents"
    BRANCHES = "branches"
    PRACTITIONERS = "practitioners"
    PATIENTS = "patients"
    APPOINTMENTS = "appointments"
    CALLS = "calls"
    CALL_SESSIONS = "call_sessions"
    OUTBOUND_CALLS = "outbound_calls"
    FOLLOWUPS = "followups"
    CALL_LATENCY_METRICS = "call_latency_metrics"
    PMS_APPOINTMENTS = "pms_appointments"
