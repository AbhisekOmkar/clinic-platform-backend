from fastapi import APIRouter, Query

from app.services.availability_service import availability_service

router = APIRouter(tags=["Availability"])


@router.get("/availability")
async def search_availability(
    date_from: str | None = Query(default=None, description="YYYY-MM-DD (clinic local)"),
    date_to: str | None = Query(default=None, description="YYYY-MM-DD inclusive"),
    branch_id: str | None = None,
    practitioner_id: str | None = None,
    specialty: str | None = None,
    weekdays: str | None = Query(
        default=None, description="Comma list e.g. 'monday,wednesday' for recurring preferences"
    ),
    after_time: str | None = Query(default=None, description="HH:MM lower bound (local)"),
    before_time: str | None = Query(default=None, description="HH:MM upper bound (local)"),
    near_time: str | None = Query(default=None, description="HH:MM anchor; slots sorted by closeness"),
    scope: str = Query(default="list", pattern="^(list|earliest)$"),
    limit: int = Query(default=12, ge=1, le=50),
):
    weekday_list = [w for w in (weekdays.split(",") if weekdays else []) if w.strip()]
    return await availability_service.search(
        date_from=date_from,
        date_to=date_to,
        branch_id=branch_id,
        practitioner_id=practitioner_id,
        specialty=specialty,
        weekdays=weekday_list or None,
        after_time=after_time,
        before_time=before_time,
        near_time=near_time,
        scope=scope,
        limit=limit,
    )
