"""End-to-end PSTN wiring: Twilio Elastic SIP Trunking -> LiveKit SIP -> agent.

Provisions BOTH sides, idempotently:

Twilio (trunking.twilio.com):
  1. an Elastic SIP Trunk named 'clinic-voice-livekit'
  2. an Origination URL pointing at this LiveKit project's SIP endpoint
     (sip:<project-subdomain>.sip.livekit.cloud;transport=tcp — per LiveKit's
     Twilio guide, TCP avoids UDP fragmentation of large INVITEs)
  3. attaches TWILIO_PHONE_NUMBER to the trunk (this replaces any previous
     voice-webhook routing on the number)

LiveKit (project from LIVEKIT_URL):
  4. an inbound SIP trunk restricted to that number
  5. a dispatch rule: each call gets its own room (prefix 'sip-clinic') with
     the clinic agent auto-dispatched (direction=inbound metadata)

Requires in .env: TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER,
LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET, WORKER_AGENT_NAME.

Run: poetry run python scripts/setup_twilio_sip.py
"""

import asyncio
import base64
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402
from livekit import api  # noqa: E402
from livekit.protocol import sip as sip_proto  # noqa: E402
from livekit.protocol.agent_dispatch import RoomAgentDispatch  # noqa: E402
from livekit.protocol.room import RoomConfiguration  # noqa: E402

from app.config.settings import settings  # noqa: E402

TRUNK_NAME = "clinic-voice-livekit"
LK_TRUNK_NAME = "clinic-inbound-twilio"
ROOM_PREFIX = "sip-clinic"


def twilio_env() -> tuple[str, str, str]:
    sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
    token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    number = os.environ.get("TWILIO_PHONE_NUMBER", "")
    if not (sid and token and number):
        print("Set TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_PHONE_NUMBER in .env")
        raise SystemExit(1)
    return sid, token, number


def sip_host() -> str:
    # LiveKit Cloud assigns each project a dedicated SIP subdomain that is
    # only shown in the dashboard (Settings -> SIP URI). It is NOT the wss
    # subdomain — set LIVEKIT_SIP_HOST (or pass as argv[1]) with that value,
    # e.g. "abc1def23gh.sip.livekit.cloud".
    override = (sys.argv[1] if len(sys.argv) > 1 else "") or os.environ.get("LIVEKIT_SIP_HOST", "")
    if override:
        return re.sub(r"^sip:", "", override.strip()).split(";")[0]
    subdomain = re.sub(r"^wss?://", "", settings.livekit_url).split(".")[0]
    print(f"WARNING: LIVEKIT_SIP_HOST not set — guessing {subdomain}.sip.livekit.cloud "
          "(LiveKit Cloud usually needs the dashboard's SIP URI instead)")
    return f"{subdomain}.sip.livekit.cloud"


async def setup_twilio(client: httpx.AsyncClient, number: str) -> None:
    trunks = (await client.get("https://trunking.twilio.com/v1/Trunks")).json()["trunks"]
    trunk = next((t for t in trunks if t["friendly_name"] == TRUNK_NAME), None)
    if trunk is None:
        response = await client.post(
            "https://trunking.twilio.com/v1/Trunks", data={"FriendlyName": TRUNK_NAME}
        )
        response.raise_for_status()
        trunk = response.json()
        print(f"twilio: created trunk {trunk['sid']}")
    else:
        print(f"twilio: trunk exists {trunk['sid']}")
    trunk_sid = trunk["sid"]

    origination_uri = f"sip:{sip_host()};transport=tcp"
    urls = (
        await client.get(f"https://trunking.twilio.com/v1/Trunks/{trunk_sid}/OriginationUrls")
    ).json()["origination_urls"]
    if any(u["sip_url"] == origination_uri for u in urls):
        print(f"twilio: origination already -> {origination_uri}")
    else:
        # Drop stale origination URLs (e.g. a previously-guessed host) so the
        # trunk always points at exactly one LiveKit endpoint.
        for stale in urls:
            await client.delete(
                f"https://trunking.twilio.com/v1/Trunks/{trunk_sid}/OriginationUrls/{stale['sid']}"
            )
            print(f"twilio: removed stale origination {stale['sip_url']}")
        response = await client.post(
            f"https://trunking.twilio.com/v1/Trunks/{trunk_sid}/OriginationUrls",
            data={
                "FriendlyName": "livekit-cloud",
                "SipUrl": origination_uri,
                "Priority": "10",
                "Weight": "10",
                "Enabled": "true",
            },
        )
        response.raise_for_status()
        print(f"twilio: origination -> {origination_uri}")

    numbers = (
        await client.get("https://api.twilio.com/2010-04-01/Accounts/"
                         f"{os.environ['TWILIO_ACCOUNT_SID']}/IncomingPhoneNumbers.json")
    ).json()["incoming_phone_numbers"]
    record = next((n for n in numbers if n["phone_number"] == number), None)
    if record is None:
        print(f"twilio: number {number} not found on this account")
        raise SystemExit(1)
    if record.get("trunk_sid") != trunk_sid:
        response = await client.post(
            f"https://trunking.twilio.com/v1/Trunks/{trunk_sid}/PhoneNumbers",
            data={"PhoneNumberSid": record["sid"]},
        )
        response.raise_for_status()
        print(f"twilio: attached {number} to trunk")
    else:
        print(f"twilio: {number} already attached")


async def setup_livekit(number: str) -> None:
    lk = api.LiveKitAPI(
        settings.livekit_url.replace("wss://", "https://"),
        settings.livekit_api_key,
        settings.livekit_api_secret,
    )
    try:
        trunks = await lk.sip.list_sip_inbound_trunk(sip_proto.ListSIPInboundTrunkRequest())
        trunk = next((t for t in trunks.items if t.name == LK_TRUNK_NAME), None)
        if trunk is None:
            trunk = await lk.sip.create_sip_inbound_trunk(
                sip_proto.CreateSIPInboundTrunkRequest(
                    trunk=sip_proto.SIPInboundTrunkInfo(
                        name=LK_TRUNK_NAME,
                        numbers=[number],
                        krisp_enabled=True,
                    )
                )
            )
            print(f"livekit: created inbound trunk {trunk.sip_trunk_id} for {number}")
        else:
            print(f"livekit: inbound trunk exists {trunk.sip_trunk_id}")

        rules = await lk.sip.list_sip_dispatch_rule(sip_proto.ListSIPDispatchRuleRequest())
        existing = next(
            (r for r in rules.items if trunk.sip_trunk_id in list(r.trunk_ids)), None
        )
        if existing is None:
            rule = await lk.sip.create_sip_dispatch_rule(
                sip_proto.CreateSIPDispatchRuleRequest(
                    rule=sip_proto.SIPDispatchRule(
                        dispatch_rule_individual=sip_proto.SIPDispatchRuleIndividual(
                            room_prefix=ROOM_PREFIX
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
            print(f"livekit: dispatch rule {rule.sip_dispatch_rule_id} -> agent '{settings.worker_agent_name}'")
        else:
            print(f"livekit: dispatch rule exists {existing.sip_dispatch_rule_id}")
    finally:
        await lk.aclose()


async def main() -> None:
    sid, token, number = twilio_env()
    auth = base64.b64encode(f"{sid}:{token}".encode()).decode()
    async with httpx.AsyncClient(headers={"Authorization": f"Basic {auth}"}, timeout=30) as client:
        await setup_twilio(client, number)
    await setup_livekit(number)
    print(f"\nDone. Calls to {number} now ring the clinic agent (rooms '{ROOM_PREFIX}-*').")


if __name__ == "__main__":
    asyncio.run(main())
