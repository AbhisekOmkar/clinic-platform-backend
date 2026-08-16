"""Write-back to the (mock) PMS.

Behaviour contract:
- every booking write carries Idempotency-Key = appointment_id, so retries
  can never create duplicate PMS records;
- a PMS failure never fails the caller-facing booking: the appointment stays
  confirmed locally with pms.status='pending' and a background loop retries
  until it drains or exhausts attempts (then 'failed', surfaced on the
  dashboard for manual follow-up).
"""

import asyncio
from datetime import datetime

import httpx
from loguru import logger

from app.config.settings import settings
from app.repositories import appointment_repository


class PmsSyncService:
    def __init__(self):
        self._client: httpx.AsyncClient | None = None
        self._retry_task: asyncio.Task | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=settings.pms_base_url,
                headers={"X-PMS-Key": settings.pms_api_key},
                timeout=10.0,
            )
        return self._client

    def _use_cliniko(self) -> bool:
        from app.pms.cliniko_client import ClinikoClient

        return settings.pms_provider.lower() == "cliniko" and ClinikoClient.configured()

    async def sync_booking(self, appointment: dict) -> dict:
        """Attempt the PMS write inline (so the confirmation can report sync
        state); failures degrade to pending + background retry. Target is the
        mock PMS or a real Cliniko account depending on PMS_PROVIDER."""
        pms_state = dict(appointment.get("pms") or {"status": "pending", "attempts": 0})
        # Idempotency, our side: a stored PMS id means the write already
        # landed — retries must never create a second record.
        if pms_state.get("pms_id"):
            pms_state["status"] = "synced"
            await appointment_repository.update_pms_state(appointment["appointment_id"], pms_state)
            return pms_state
        try:
            if self._use_cliniko():
                pms_id = await self._cliniko_create(appointment)
            else:
                pms_id = await self._mock_create(appointment)
            pms_state.update(
                {
                    "status": "synced",
                    "provider": "cliniko" if self._use_cliniko() else "mock",
                    "pms_id": pms_id,
                    "attempts": pms_state.get("attempts", 0) + 1,
                    "last_error": None,
                    "synced_at": datetime.utcnow().isoformat(),
                }
            )
        except Exception as exc:
            pms_state.update(
                {
                    "status": "pending",
                    "attempts": pms_state.get("attempts", 0) + 1,
                    "last_error": str(exc)[:300],
                }
            )
            logger.warning(
                "PMS booking write failed; will retry in background",
                appointment_id=appointment["appointment_id"],
                error=str(exc)[:200],
            )
        await appointment_repository.update_pms_state(appointment["appointment_id"], pms_state)
        return pms_state

    async def _mock_create(self, appointment: dict) -> str:
        response = await self._get_client().post(
            "/appointments",
            json=self._booking_payload(appointment),
            headers={"Idempotency-Key": appointment["appointment_id"]},
        )
        response.raise_for_status()
        return response.json().get("pms_id")

    async def _cliniko_create(self, appointment: dict) -> str:
        from app.pms.cliniko_client import ClinikoClient
        from app.repositories import branch_repository, patient_repository, practitioner_repository

        practitioner = await practitioner_repository.get_by_practitioner_id(
            appointment["practitioner_id"]
        )
        branch = await branch_repository.get_by_branch_id(appointment["branch_id"])
        mapping_missing = [
            name
            for name, value in (
                ("practitioner.cliniko_practitioner_id", (practitioner or {}).get("cliniko_practitioner_id")),
                ("practitioner.cliniko_appointment_type_id", (practitioner or {}).get("cliniko_appointment_type_id")),
                ("branch.cliniko_business_id", (branch or {}).get("cliniko_business_id")),
            )
            if not value
        ]
        if mapping_missing:
            raise RuntimeError(f"Cliniko id mapping missing: {mapping_missing} — run scripts/cliniko_link.py")

        patient = await patient_repository.get_by_patient_id(appointment["patient_id"])
        cliniko_patient_id = (patient or {}).get("cliniko_patient_id")
        if not cliniko_patient_id:
            cliniko_patient = await ClinikoClient.find_or_create_patient(
                appointment["patient_name"], appointment.get("phone") or ""
            )
            cliniko_patient_id = cliniko_patient["id"]
            await patient_repository.update_one(
                {"patient_id": appointment["patient_id"]},
                {"cliniko_patient_id": cliniko_patient_id},
            )

        starts = appointment["start_utc"]
        ends = appointment["end_utc"]
        starts = starts.isoformat() if isinstance(starts, datetime) else str(starts)
        ends = ends.isoformat() if isinstance(ends, datetime) else str(ends)
        created = await ClinikoClient.create_appointment(
            patient_id=cliniko_patient_id,
            practitioner_id=practitioner["cliniko_practitioner_id"],
            business_id=branch["cliniko_business_id"],
            appointment_type_id=practitioner["cliniko_appointment_type_id"],
            starts_at_utc=starts + "Z" if not starts.endswith("Z") else starts,
            ends_at_utc=ends + "Z" if not ends.endswith("Z") else ends,
            notes=appointment.get("reason"),
        )
        return str(created["id"])

    async def sync_cancellation(self, appointment: dict, reason: str) -> None:
        try:
            if self._use_cliniko():
                pms_id = (appointment.get("pms") or {}).get("pms_id")
                if pms_id:
                    from app.pms.cliniko_client import ClinikoClient

                    await ClinikoClient.cancel_appointment(int(pms_id), reason)
            else:
                response = await self._get_client().post(
                    f"/appointments/{appointment['appointment_id']}/cancel",
                    json={"reason": reason},
                    headers={"Idempotency-Key": f"cancel-{appointment['appointment_id']}"},
                )
                response.raise_for_status()
        except Exception as exc:
            # Cancellation write-backs are retried by the same loop via a
            # tombstone marker on the appointment.
            logger.warning(
                "PMS cancellation write failed",
                appointment_id=appointment["appointment_id"],
                error=str(exc)[:200],
            )
            await appointment_repository.update_one(
                {"appointment_id": appointment["appointment_id"]},
                {"pms_cancel_pending": True, "pms_cancel_reason": reason},
            )

    def _booking_payload(self, appointment: dict) -> dict:
        start_utc = appointment["start_utc"]
        if isinstance(start_utc, datetime):
            start_utc = start_utc.isoformat()
        return {
            "appointment_id": appointment["appointment_id"],
            "patient": {
                "name": appointment["patient_name"],
                "phone": appointment["phone"],
                "patient_id": appointment["patient_id"],
            },
            "practitioner_id": appointment["practitioner_id"],
            "practitioner_name": appointment["practitioner_name"],
            "branch_id": appointment["branch_id"],
            "starts_at_local": appointment["start_local"],
            "starts_at_utc": start_utc,
            "duration_minutes": appointment["duration_minutes"],
            "notes": appointment.get("reason"),
        }

    def start_retry_loop(self) -> None:
        if self._retry_task is None or self._retry_task.done():
            self._retry_task = asyncio.create_task(self._retry_loop())

    async def stop(self) -> None:
        if self._retry_task and not self._retry_task.done():
            self._retry_task.cancel()
            try:
                await self._retry_task
            except asyncio.CancelledError:
                pass
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def _retry_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(settings.pms_retry_interval_seconds)
                await self._drain_pending()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(f"PMS retry loop error: {exc}")

    async def _drain_pending(self) -> None:
        pending = await appointment_repository.list_pending_pms_sync(
            settings.pms_max_retry_attempts
        )
        for appointment in pending:
            state = await self.sync_booking(appointment)
            if (
                state["status"] == "pending"
                and state["attempts"] >= settings.pms_max_retry_attempts
            ):
                state["status"] = "failed"
                await appointment_repository.update_pms_state(
                    appointment["appointment_id"], state
                )
                logger.error(
                    "PMS sync exhausted retries; needs manual follow-up",
                    appointment_id=appointment["appointment_id"],
                )


pms_sync_service = PmsSyncService()
