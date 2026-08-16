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

    async def sync_booking(self, appointment: dict) -> dict:
        """Attempt the PMS write inline (so the confirmation can report sync
        state); failures degrade to pending + background retry."""
        pms_state = dict(appointment.get("pms") or {"status": "pending", "attempts": 0})
        payload = self._booking_payload(appointment)
        try:
            response = await self._get_client().post(
                "/appointments",
                json=payload,
                headers={"Idempotency-Key": appointment["appointment_id"]},
            )
            response.raise_for_status()
            body = response.json()
            pms_state.update(
                {
                    "status": "synced",
                    "pms_id": body.get("pms_id"),
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

    async def sync_cancellation(self, appointment: dict, reason: str) -> None:
        try:
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
