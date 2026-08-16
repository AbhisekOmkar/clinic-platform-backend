"""Minimal Cliniko REST client (the real PMS).

Auth is HTTP Basic with the API key as username; every request must carry a
User-Agent identifying the integration. The API shard (au1/uk1/...) is
encoded as the API key's suffix and becomes the api.<shard>.cliniko.com host.
"""

import base64
import re

import httpx
from loguru import logger

from app.config.settings import settings


def shard_base_url(api_key: str) -> str:
    if settings.cliniko_base_url:
        return settings.cliniko_base_url.rstrip("/")
    match = re.search(r"-([a-z]{2}\d+)$", api_key.strip())
    shard = match.group(1) if match else "au1"
    return f"https://api.{shard}.cliniko.com/v1"


class ClinikoClient:
    _client: httpx.AsyncClient | None = None

    @classmethod
    def configured(cls) -> bool:
        return bool(settings.cliniko_api_key)

    @classmethod
    def client(cls) -> httpx.AsyncClient:
        if cls._client is None or cls._client.is_closed:
            token = base64.b64encode(f"{settings.cliniko_api_key}:".encode()).decode()
            cls._client = httpx.AsyncClient(
                base_url=shard_base_url(settings.cliniko_api_key),
                headers={
                    "Authorization": f"Basic {token}",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "User-Agent": settings.cliniko_user_agent,
                },
                timeout=20.0,
            )
        return cls._client

    @classmethod
    async def get(cls, path: str, params: dict | None = None) -> dict:
        response = await cls.client().get(path, params=params)
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _body(response: httpx.Response) -> dict:
        # Cliniko returns 204/empty bodies on some mutations (e.g. cancel)
        if response.status_code == 204 or not response.content:
            return {}
        return response.json()

    @classmethod
    async def post(cls, path: str, json: dict) -> dict:
        response = await cls.client().post(path, json=json)
        if response.status_code >= 400:
            logger.warning(f"Cliniko POST {path} -> {response.status_code}: {response.text[:300]}")
        response.raise_for_status()
        return cls._body(response)

    @classmethod
    async def patch(cls, path: str, json: dict) -> dict:
        response = await cls.client().patch(path, json=json)
        if response.status_code >= 400:
            logger.warning(f"Cliniko PATCH {path} -> {response.status_code}: {response.text[:300]}")
        response.raise_for_status()
        return cls._body(response)

    @classmethod
    async def aclose(cls) -> None:
        if cls._client and not cls._client.is_closed:
            await cls._client.aclose()

    # ---- domain helpers -------------------------------------------------

    @classmethod
    async def list_all(cls, path: str, key: str) -> list[dict]:
        """Follow Cliniko pagination links."""
        items: list[dict] = []
        url: str | None = path
        while url:
            data = await cls.get(url)
            items.extend(data.get(key, []))
            url = (data.get("links") or {}).get("next")
            if url:
                url = url.replace(shard_base_url(settings.cliniko_api_key), "")
        return items

    @classmethod
    async def find_or_create_patient(cls, full_name: str, phone: str) -> dict:
        parts = full_name.strip().split()
        first = parts[0]
        last = " ".join(parts[1:]) or "-"
        # Exact-name search, then phone check client-side (phone filters are
        # unreliable across shapes; names + our own stored ids do the work).
        result = await cls.get(
            "/patients",
            params={"q[]": [f"first_name:={first}", f"last_name:={last}"]},
        )
        for patient in result.get("patients", []):
            numbers = [p.get("number", "") for p in patient.get("patient_phone_numbers", [])]
            if not phone or any(phone[-10:] in (n or "") for n in numbers):
                return patient
        payload: dict = {"first_name": first, "last_name": last}
        if phone:
            payload["patient_phone_numbers"] = [
                {"number": phone, "phone_type": "Mobile"}
            ]
        return await cls.post("/patients", payload)

    @classmethod
    async def create_appointment(
        cls,
        *,
        patient_id: int,
        practitioner_id: int,
        business_id: int,
        appointment_type_id: int,
        starts_at_utc: str,
        ends_at_utc: str,
        notes: str | None = None,
    ) -> dict:
        payload = {
            "patient_id": patient_id,
            "practitioner_id": practitioner_id,
            "business_id": business_id,
            "appointment_type_id": appointment_type_id,
            "starts_at": starts_at_utc,
            "ends_at": ends_at_utc,
        }
        if notes:
            payload["notes"] = notes
        return await cls.post("/individual_appointments", payload)

    @classmethod
    async def cancel_appointment(cls, appointment_id: int, reason: str | None = None) -> None:
        # Cliniko cancels via PATCH /individual_appointments/{id}/cancel
        # (cancellation_reason codes: 50 = "Other").
        try:
            await cls.patch(
                f"/individual_appointments/{appointment_id}/cancel",
                {"cancellation_reason": 50, "cancellation_note": (reason or "cancelled")[:250]},
            )
        except httpx.HTTPStatusError:
            # Fallback: archive if the cancel endpoint shape differs
            response = await cls.client().delete(f"/individual_appointments/{appointment_id}")
            response.raise_for_status()
