from datetime import datetime

from pymongo.errors import DuplicateKeyError

from app.db.mongodb import Collections, get_database
from app.repositories.base import BaseRepository
from app.utils.serializers import serialize_doc

ACTIVE_STATUSES = ["confirmed"]


class SlotTakenError(Exception):
    """Raised when the write-time uniqueness guard rejects a booking."""


class AppointmentRepository(BaseRepository):
    def __init__(self):
        super().__init__(lambda: get_database()[Collections.APPOINTMENTS])

    async def insert_booking(self, appointment: dict) -> dict:
        """Insert relying on the partial unique index over
        (practitioner_id, start_utc) for status='confirmed'. This is the
        write-time double-booking guard: two racing inserts for the same slot
        cannot both succeed, no matter what availability said earlier."""
        appointment.setdefault("created_at", datetime.utcnow())
        appointment.setdefault("deleted_at", None)
        try:
            await self.collection.insert_one(appointment)
        except DuplicateKeyError as exc:
            raise SlotTakenError(
                f"Slot {appointment.get('start_local')} already booked for "
                f"{appointment.get('practitioner_name')}"
            ) from exc
        return serialize_doc(appointment)

    async def get_by_appointment_id(self, appointment_id: str) -> dict | None:
        return await self.find_one({"appointment_id": appointment_id})

    async def find_active_overlapping(
        self,
        practitioner_id: str,
        window_start_utc: datetime,
        window_end_utc: datetime,
        exclude_appointment_id: str | None = None,
    ) -> list[dict]:
        """Active appointments whose [start,end) intersects the given UTC window."""
        filter = {
            "practitioner_id": practitioner_id,
            "status": {"$in": ACTIVE_STATUSES},
            "start_utc": {"$lt": window_end_utc},
            "end_utc": {"$gt": window_start_utc},
        }
        if exclude_appointment_id:
            filter["appointment_id"] = {"$ne": exclude_appointment_id}
        return await self.find_many(filter)

    async def list_active_for_practitioners(
        self,
        practitioner_ids: list[str],
        range_start_utc: datetime,
        range_end_utc: datetime,
    ) -> list[dict]:
        return await self.find_many(
            {
                "practitioner_id": {"$in": practitioner_ids},
                "status": {"$in": ACTIVE_STATUSES},
                "start_utc": {"$lt": range_end_utc},
                "end_utc": {"$gt": range_start_utc},
            },
            limit=2000,
        )

    async def list_for_phone(self, phone: str, upcoming_only_after_utc: datetime | None = None) -> list[dict]:
        filter: dict = {"phone": phone, "status": {"$in": ACTIVE_STATUSES}}
        if upcoming_only_after_utc is not None:
            filter["start_utc"] = {"$gte": upcoming_only_after_utc}
        return await self.find_many(filter, sort=[("start_utc", 1)])

    async def list_for_patient(self, patient_id: str) -> list[dict]:
        return await self.find_many({"patient_id": patient_id}, sort=[("start_utc", -1)])

    async def list_recent(self, limit: int = 100) -> list[dict]:
        return await self.find_many({}, sort=[("start_utc", -1)], limit=limit)

    async def set_status(
        self, appointment_id: str, status: str, extra: dict | None = None
    ) -> bool:
        update = {"status": status}
        if extra:
            update.update(extra)
        return await self.update_one({"appointment_id": appointment_id}, update)

    async def update_pms_state(self, appointment_id: str, pms: dict) -> bool:
        return await self.update_one({"appointment_id": appointment_id}, {"pms": pms})

    async def list_pending_pms_sync(self, max_attempts: int) -> list[dict]:
        return await self.find_many(
            {
                "pms.status": "pending",
                "pms.attempts": {"$lt": max_attempts},
            },
            limit=50,
        )


appointment_repository = AppointmentRepository()
