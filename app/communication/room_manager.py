"""LiveKit server-side operations: web-call rooms + agent dispatch.

Pattern: construct LiveKitAPI per call, always aclose() in finally.
Browser tokens get the narrowest grants that still allow a two-way call.
"""

import json

from livekit import api
from loguru import logger

from app.config.settings import settings


class RoomManager:
    def _api(self) -> api.LiveKitAPI:
        return api.LiveKitAPI(
            settings.livekit_url.replace("wss://", "https://"),
            settings.livekit_api_key,
            settings.livekit_api_secret,
        )

    def mint_user_token(self, room_name: str, identity: str) -> str:
        token = (
            api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
            .with_identity(identity)
            .with_name("caller")
            .with_grants(
                api.VideoGrants(
                    room_join=True,
                    room=room_name,
                    can_publish=True,
                    can_subscribe=True,
                    can_publish_data=True,
                )
            )
        )
        return token.to_jwt()

    async def create_room_with_agent(self, room_name: str, job_metadata: dict) -> None:
        lk = self._api()
        try:
            await lk.room.create_room(api.CreateRoomRequest(name=room_name))
            await lk.agent_dispatch.create_dispatch(
                api.CreateAgentDispatchRequest(
                    room=room_name,
                    agent_name=settings.worker_agent_name,
                    metadata=json.dumps(job_metadata),
                )
            )
            logger.info(
                "Room created and agent dispatched",
                room=room_name,
                agent=settings.worker_agent_name,
            )
        finally:
            await lk.aclose()

    async def delete_room(self, room_name: str) -> None:
        lk = self._api()
        try:
            await lk.room.delete_room(api.DeleteRoomRequest(room=room_name))
        except Exception as exc:
            logger.warning(f"delete_room({room_name}): {exc}")
        finally:
            await lk.aclose()

    _outbound_trunk_id: str | None = None

    async def outbound_trunk_id(self) -> str:
        """Resolve (and cache) the provisioned PSTN outbound trunk by name."""
        if self._outbound_trunk_id:
            return self._outbound_trunk_id
        from livekit.protocol import sip as sip_proto

        lk = self._api()
        try:
            trunks = await lk.sip.list_sip_outbound_trunk(sip_proto.ListSIPOutboundTrunkRequest())
            trunk = next(
                (t for t in trunks.items if t.name == "clinic-outbound-twilio"), None
            )
            if trunk is None:
                raise RuntimeError(
                    "No outbound SIP trunk 'clinic-outbound-twilio' — run scripts/setup_twilio_sip.py"
                )
            self._outbound_trunk_id = trunk.sip_trunk_id
            return self._outbound_trunk_id
        finally:
            await lk.aclose()

    async def dial_out(self, room_name: str, to_number: str, identity: str) -> str:
        """Add a PSTN participant to the room (rings the patient's phone)."""
        trunk_id = await self.outbound_trunk_id()
        lk = self._api()
        try:
            participant = await lk.sip.create_sip_participant(
                api.CreateSIPParticipantRequest(
                    sip_trunk_id=trunk_id,
                    sip_call_to=to_number,
                    room_name=room_name,
                    participant_identity=identity,
                    participant_name="patient",
                    krisp_enabled=True,
                )
            )
            logger.info(f"Dialing {to_number} into {room_name} (sip_call_id={participant.sip_call_id})")
            return participant.sip_call_id
        finally:
            await lk.aclose()


room_manager = RoomManager()
