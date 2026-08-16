from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from loguru import logger

from app.config.logging import setup_logging
from app.config.settings import settings
from app.db.mongodb import close_mongo_connection, connect_to_mongo
from app.services.pms_sync_service import pms_sync_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    logger.info(f"Starting clinic platform backend on {settings.host}:{settings.port}")
    await connect_to_mongo()
    pms_sync_service.start_retry_loop()
    yield
    await pms_sync_service.stop()
    await close_mongo_connection()


app = FastAPI(
    title="Clinic Platform Backend",
    description="Scheduling engine, patient records, call state and mock PMS for the voice AI receptionist",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def internal_auth_middleware(request: Request, call_next):
    """When INTERNAL_SERVICE_KEY is set, every /api/v1 route requires it.
    /pms/* has its own key check; docs and health stay public."""
    if settings.internal_service_key and request.url.path.startswith("/api/v1"):
        provided = request.headers.get("X-Internal-Service-Key")
        if provided != settings.internal_service_key:
            return JSONResponse(status_code=401, content={"message": "Invalid or missing X-Internal-Service-Key"})
    return await call_next(request)


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"message": "Validation error", "errors": exc.errors()})


@app.exception_handler(Exception)
async def unhandled_handler(request: Request, exc: Exception):
    logger.exception(f"Unhandled error on {request.url.path}")
    return JSONResponse(status_code=500, content={"message": "Internal server error"})


@app.get("/health")
async def health():
    return {"status": "ok", "service": "clinic-platform-backend"}


@app.get("/")
async def root():
    return {"service": "clinic-platform-backend", "docs": "/docs"}


from app.pms.router import router as pms_router  # noqa: E402
from app.routes import router as api_router  # noqa: E402

app.include_router(api_router)
app.include_router(pms_router)
