import os

os.environ["MONGODB_DATABASE"] = "clinic_platform_test"
os.environ["INTERNAL_SERVICE_KEY"] = ""
os.environ["PMS_CHAOS_FAILURE_RATE"] = "0.0"

import httpx  # noqa: E402
import pytest  # noqa: E402

from app.db.mongodb import close_mongo_connection, connect_to_mongo, get_database  # noqa: E402
from app.main import app  # noqa: E402
from app.services.pms_sync_service import pms_sync_service  # noqa: E402
from scripts.create_indexes import create_indexes  # noqa: E402
from scripts.seed_clinic import seed  # noqa: E402

TEST_DB = "clinic_platform_test"


@pytest.fixture(scope="session", autouse=True)
async def _database():
    await connect_to_mongo(TEST_DB)
    db = get_database()
    await db.client.drop_database(TEST_DB)
    await seed(TEST_DB)
    await create_indexes(TEST_DB)
    yield
    await db.client.drop_database(TEST_DB)
    await close_mongo_connection()


@pytest.fixture(autouse=True)
async def _pms_client_over_asgi():
    """Route the backend's PMS write-backs through the in-process ASGI app so
    tests exercise the real mock-PMS endpoints without a network listener."""
    from app.config.settings import settings

    pms_sync_service._client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver/pms/api/v1",
        headers={"X-PMS-Key": settings.pms_api_key},
        timeout=10.0,
    )
    yield
    await pms_sync_service._client.aclose()
    pms_sync_service._client = None


@pytest.fixture
async def client():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as c:
        yield c


@pytest.fixture
async def db():
    return get_database()


@pytest.fixture
async def clean_bookings(db):
    """Reset dynamic collections between tests (clinic catalog stays)."""
    for coll in (
        "appointments",
        "patients",
        "calls",
        "call_sessions",
        "outbound_calls",
        "followups",
        "pms_appointments",
    ):
        await db[coll].delete_many({})
    yield
