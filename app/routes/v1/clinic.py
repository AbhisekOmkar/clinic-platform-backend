from fastapi import APIRouter, HTTPException

from app.repositories import branch_repository, practitioner_repository

router = APIRouter(tags=["Clinic"])


@router.get("/branches")
async def list_branches():
    return {"branches": await branch_repository.list_all()}


@router.get("/practitioners")
async def list_practitioners(
    specialty: str | None = None,
    branch_id: str | None = None,
    name: str | None = None,
):
    practitioners = await practitioner_repository.search(
        specialty=specialty, branch_id=branch_id, name_contains=name
    )
    return {"practitioners": practitioners}


@router.get("/practitioners/{practitioner_id}")
async def get_practitioner(practitioner_id: str):
    practitioner = await practitioner_repository.get_by_practitioner_id(practitioner_id)
    if practitioner is None:
        raise HTTPException(status_code=404, detail="Practitioner not found")
    return practitioner


@router.get("/specialties")
async def list_specialties():
    return {"specialties": await practitioner_repository.distinct_specialties()}
