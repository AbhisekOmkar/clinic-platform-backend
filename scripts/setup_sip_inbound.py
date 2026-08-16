"""Attach a PSTN number to the voice agent.

Creates a LiveKit inbound SIP trunk for the given number and a dispatch rule
that drops every inbound call into its own room with the clinic agent
dispatched automatically. Point your Twilio/Telnyx/Plivo SIP trunk at your
LiveKit project's SIP URI (see LiveKit Cloud -> Settings -> SIP), then run:

    poetry run python scripts/setup_sip_inbound.py +91XXXXXXXXXX

The worker reads the caller's number from sip.phoneNumber and everything else
(returning-patient recognition, dropped-call resume) works exactly as it does
for web calls.
"""

import asyncio
import json
import sys

from livekit import api
from livekit.protocol import sip as sip_proto
from livekit.protocol.agent_dispatch import RoomAgentDispatch
from livekit.protocol.room import RoomConfiguration

from app.config.settings import settings


async def main(number: str) -> None:
    lk = api.LiveKitAPI(
        settings.livekit_url.replace("wss://", "https://"),
        settings.livekit_api_key,
        settings.livekit_api_secret,
    )
    try:
        trunk = await lk.sip.create_sip_inbound_trunk(
            sip_proto.CreateSIPInboundTrunkRequest(
                trunk=sip_proto.SIPInboundTrunkInfo(
                    name=f"clinic-inbound-{number[-4:]}",
                    numbers=[number],
                )
            )
        )
        print(f"Inbound trunk: {trunk.sip_trunk_id}")

        rule = await lk.sip.create_sip_dispatch_rule(
            sip_proto.CreateSIPDispatchRuleRequest(
                rule=sip_proto.SIPDispatchRule(
                    dispatch_rule_individual=sip_proto.SIPDispatchRuleIndividual(
                        room_prefix="sip-clinic"
                    )
                ),
                trunk_ids=[trunk.sip_trunk_id],
                room_config=RoomConfiguration(
                    agents=[
                        RoomAgentDispatch(
                            agent_name=settings.worker_agent_name,
                            metadata=json.dumps({"direction": "inbound"}),
                        )
                    ]
                ),
            )
        )
        print(f"Dispatch rule: {rule.sip_dispatch_rule_id}")
        print("Done. Calls to", number, "now reach the clinic agent.")
    finally:
        await lk.aclose()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
