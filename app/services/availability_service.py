"""Slot computation engine.

Availability is always derived fresh from practitioner schedules minus live
bookings (including buffer rules) — never cached — so the agent's
"re-check against live data" behaviour is backed by an endpoint that cannot
serve stale state. Every response carries `as_of` so downstream layers can
prove when a quote was computed.
"""

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta

from fastapi import HTTPException

from app.config.settings import settings
from app.repositories import (
    appointment_repository,
    branch_repository,
    practitioner_repository,
)
from app.utils import timeutil

WEEKDAY_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


@dataclass
class Slot:
    practitioner_id: str
    practitioner_name: str
    specialty: str
    branch_id: str
    branch_name: str
    date_local: str
    start_hm: str
    end_hm: str
    duration_minutes: int
    fee_inr: int
    display: str
    start_utc: datetime | None = None  # excluded from API payload


def _slot_public(slot: Slot) -> dict:
    data = asdict(slot)
    data.pop("start_utc", None)
    return data


class AvailabilityService:
    async def search(
        self,
        date_from: str | None = None,
        date_to: str | None = None,
        branch_id: str | None = None,
        practitioner_id: str | None = None,
        specialty: str | None = None,
        weekdays: list[str] | None = None,
        after_time: str | None = None,
        before_time: str | None = None,
        near_time: str | None = None,
        scope: str = "list",
        limit: int = 12,
    ) -> dict:
        """Compute open slots. scope='earliest' returns the global soonest
        slots across every matching practitioner and branch (sorted purely by
        time), which is what "earliest available" questions must use."""
        now_local = timeutil.now_local()
        min_start = now_local + timedelta(minutes=settings.min_booking_notice_minutes)

        start_date = (
            timeutil.parse_local_date(date_from) if date_from else timeutil.today_local()
        )
        if date_to:
            end_date = timeutil.parse_local_date(date_to)
        elif date_from:
            end_date = start_date
        else:
            end_date = start_date + timedelta(days=settings.availability_horizon_days - 1)
        if end_date < start_date:
            raise HTTPException(status_code=400, detail="date_to is before date_from")
        # Cap the scan window to keep responses bounded
        end_date = min(end_date, timeutil.today_local() + timedelta(days=60))

        weekday_filter = self._parse_weekdays(weekdays)

        practitioners = await self._resolve_practitioners(practitioner_id, specialty, branch_id)
        if not practitioners:
            return self._empty_result(
                "No practitioner matches that filter",
                specialty=specialty,
                branch_id=branch_id,
            )
        branches = {b["branch_id"]: b for b in await branch_repository.list_all()}

        range_start_utc = timeutil.local_to_utc(timeutil.combine_local(start_date, "00:00"))
        range_end_utc = timeutil.local_to_utc(
            timeutil.combine_local(end_date + timedelta(days=1), "00:00")
        )
        booked = await appointment_repository.list_active_for_practitioners(
            [p["practitioner_id"] for p in practitioners], range_start_utc, range_end_utc
        )
        booked_by_practitioner: dict[str, list[tuple[datetime, datetime, int]]] = {}
        for appt in booked:
            start = datetime.fromisoformat(appt["start_utc"])
            end = datetime.fromisoformat(appt["end_utc"])
            booked_by_practitioner.setdefault(appt["practitioner_id"], []).append(
                (start, end, 0)
            )

        slots: list[Slot] = []
        day = start_date
        while day <= end_date:
            if not weekday_filter or day.weekday() in weekday_filter:
                for practitioner in practitioners:
                    slots.extend(
                        self._slots_for_day(
                            practitioner,
                            branches,
                            day,
                            branch_id,
                            min_start,
                            booked_by_practitioner.get(practitioner["practitioner_id"], []),
                            after_time,
                            before_time,
                        )
                    )
            day += timedelta(days=1)

        slots.sort(key=lambda s: (s.start_utc, s.practitioner_name))

        if near_time and slots:
            anchor = timeutil.minutes_of_day(near_time)
            slots.sort(
                key=lambda s: (
                    s.date_local,
                    abs(timeutil.minutes_of_day(s.start_hm) - anchor),
                )
            )

        payload_slots = [_slot_public(s) for s in slots[: max(1, min(limit, 50))]]
        return {
            "as_of": now_local.isoformat(),
            "timezone": settings.clinic_timezone,
            "today": timeutil.today_local().isoformat(),
            "scope": scope,
            "query": {
                "date_from": start_date.isoformat(),
                "date_to": end_date.isoformat(),
                "branch_id": branch_id,
                "practitioner_id": practitioner_id,
                "specialty": specialty,
                "weekdays": weekdays,
                "after_time": after_time,
                "before_time": before_time,
                "near_time": near_time,
            },
            "total_matching": len(slots),
            "slots": payload_slots,
        }

    async def _resolve_practitioners(
        self, practitioner_id: str | None, specialty: str | None, branch_id: str | None
    ) -> list[dict]:
        if practitioner_id:
            practitioner = await practitioner_repository.get_by_practitioner_id(practitioner_id)
            return [practitioner] if practitioner else []
        return await practitioner_repository.search(specialty=specialty, branch_id=branch_id)

    def _parse_weekdays(self, weekdays: list[str] | None) -> set[int] | None:
        if not weekdays:
            return None
        result: set[int] = set()
        for name in weekdays:
            key = name.strip().lower()[:3]
            for idx, full in enumerate(WEEKDAY_NAMES):
                if full.startswith(key):
                    result.add(idx)
                    break
            else:
                raise HTTPException(status_code=400, detail=f"Unknown weekday '{name}'")
        return result

    def _slots_for_day(
        self,
        practitioner: dict,
        branches: dict[str, dict],
        day: date,
        branch_filter: str | None,
        min_start_local: datetime,
        booked: list[tuple[datetime, datetime, int]],
        after_time: str | None,
        before_time: str | None,
    ) -> list[Slot]:
        slots: list[Slot] = []
        slot_minutes = practitioner.get("slot_minutes", 15)
        buffer_minutes = practitioner.get("buffer_minutes", 0)
        after_min = timeutil.minutes_of_day(after_time) if after_time else None
        before_min = timeutil.minutes_of_day(before_time) if before_time else None

        for entry in practitioner.get("weekly_schedule", []):
            if entry["weekday"] != day.weekday():
                continue
            if branch_filter and entry["branch_id"] != branch_filter:
                continue
            branch = branches.get(entry["branch_id"])
            if branch is None:
                continue
            for block in entry.get("blocks", []):
                start_min = timeutil.minutes_of_day(block["start"])
                end_min = timeutil.minutes_of_day(block["end"])
                cursor = start_min
                while cursor + slot_minutes <= end_min:
                    hm = timeutil.hm_of_minutes(cursor)
                    slot_local = timeutil.combine_local(day, hm)
                    if slot_local < min_start_local:
                        cursor += slot_minutes
                        continue
                    if after_min is not None and cursor < after_min:
                        cursor += slot_minutes
                        continue
                    if before_min is not None and cursor + slot_minutes > before_min:
                        break
                    slot_start_utc = timeutil.local_to_utc(slot_local)
                    slot_end_utc = slot_start_utc + timedelta(minutes=slot_minutes)
                    if not self._conflicts(
                        slot_start_utc, slot_end_utc, buffer_minutes, booked
                    ):
                        slots.append(
                            Slot(
                                practitioner_id=practitioner["practitioner_id"],
                                practitioner_name=practitioner["full_name"],
                                specialty=practitioner["specialty"],
                                branch_id=branch["branch_id"],
                                branch_name=branch["name"],
                                date_local=day.isoformat(),
                                start_hm=hm,
                                end_hm=timeutil.hm_of_minutes(cursor + slot_minutes),
                                duration_minutes=slot_minutes,
                                fee_inr=practitioner.get("fee_inr", 0),
                                display=timeutil.format_slot_display(day, hm),
                                start_utc=slot_start_utc,
                            )
                        )
                    cursor += slot_minutes
        return slots

    @staticmethod
    def _conflicts(
        start_utc: datetime,
        end_utc: datetime,
        buffer_minutes: int,
        booked: list[tuple[datetime, datetime, int]],
    ) -> bool:
        """A candidate slot conflicts when it overlaps an existing booking
        expanded by the practitioner's buffer on both sides — same-day slots
        must not pack back-to-back when the clinic requires a gap."""
        pad = timedelta(minutes=buffer_minutes)
        for booked_start, booked_end, _ in booked:
            if start_utc < booked_end + pad and end_utc > booked_start - pad:
                return True
        return False

    def _empty_result(self, reason: str, **query) -> dict:
        return {
            "as_of": timeutil.now_local().isoformat(),
            "timezone": settings.clinic_timezone,
            "today": timeutil.today_local().isoformat(),
            "scope": "list",
            "query": query,
            "total_matching": 0,
            "slots": [],
            "note": reason,
        }


availability_service = AvailabilityService()
