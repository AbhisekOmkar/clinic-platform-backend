from fastapi import APIRouter

from app.routes.v1 import admin, agent, appointments, availability, calls, clinic, webcall

router = APIRouter(prefix="/api/v1")
router.include_router(admin.router)
router.include_router(clinic.router)
router.include_router(availability.router)
router.include_router(appointments.router)
router.include_router(agent.router)
router.include_router(calls.router)
router.include_router(webcall.router)
