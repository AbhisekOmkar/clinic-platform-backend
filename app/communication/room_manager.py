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


room_manager = RoomManager()
