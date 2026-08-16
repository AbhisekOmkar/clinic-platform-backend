"""Voice agent configurations.

One agent is 'active' at a time (a clinic has one phone persona); the worker
resolves the active agent at call start, or a specific agent_id passed in job
metadata (the dashboard's web-call tester can pick any agent). An agent
carries the editable persona: prompt, opening line, voice, model. The
per-call context block (caller recognition, clock, roster) is always appended
by the worker and is not part of the stored prompt.
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.db.mongodb import Collections, get_database
from app.repositories.base import BaseRepository

router = APIRouter(prefix="/agents", tags=["Agents"])


class AgentRepository(BaseRepository):
    def __init__(self):
        super().__init__(lambda: get_database()[Collections.AGENTS])

    async def get_by_agent_id(self, agent_id: str) -> dict | None:
        return await self.find_one({"agent_id": agent_id})

    async def get_active(self) -> dict | None:
        return await self.find_one({"status": "active"})

    async def list_all(self) -> list[dict]:
        return await self.find_many({}, sort=[("created_at", 1)])


agent_repository = AgentRepository()


class SttConfig(BaseModel):
    provider: str = Field("deepgram", pattern="^(deepgram|openai)$")
    model: str = "nova-3"
    language: str = Field("multi", description="deepgram language mode: multi|en|hi (openai ignores)")


class LlmConfig(BaseModel):
    provider: str = Field("openai", pattern="^(openai|azure)$")
    model: str = "gpt-4.1"
    temperature: float = Field(0.3, ge=0.0, le=1.0)


class TtsConfig(BaseModel):
    provider: str = Field("cartesia", pattern="^(cartesia|openai)$")
    model: str = "sonic-3"
    voice_id: str = "95d51f79-c397-46f9-b49a-23763d3eaa2d"
    voice_label: str | None = None
    speed: float | None = Field(None, ge=0.5, le=2.0)


class CallConfig(BaseModel):
    allow_interruptions: bool = True
    min_endpointing_delay: float = Field(0.4, ge=0.1, le=3.0)
    max_endpointing_delay: float = Field(5.0, ge=1.0, le=15.0)
    enable_noise_cancellation: bool = True
    holding_phrase_after_seconds: float = Field(1.2, ge=0.3, le=5.0)


class AgentCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)
    description: str | None = None
    base_prompt: str = Field(..., min_length=20, description="Full system prompt (persona + policy)")
    opening_line: str | None = Field(None, description="Spoken greeting; empty = worker default")
    stt_config: SttConfig = Field(default_factory=SttConfig)
    llm_config: LlmConfig = Field(default_factory=LlmConfig)
    tts_config: TtsConfig = Field(default_factory=TtsConfig)
    call_config: CallConfig = Field(default_factory=CallConfig)


class AgentUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=60)
    description: str | None = None
    base_prompt: str | None = Field(None, min_length=20)
    opening_line: str | None = None
    stt_config: SttConfig | None = None
    llm_config: LlmConfig | None = None
    tts_config: TtsConfig | None = None
    call_config: CallConfig | None = None


@router.get("")
async def list_agents():
    return {"agents": await agent_repository.list_all()}


@router.get("/active")
async def get_active_agent():
    agent = await agent_repository.get_active()
    if agent is None:
        raise HTTPException(status_code=404, detail="No active agent configured")
    return agent


@router.get("/{agent_id}")
async def get_agent(agent_id: str):
    agent = await agent_repository.get_by_agent_id(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


@router.post("")
async def create_agent(body: AgentCreate):
    agent = body.model_dump()
    agent["agent_id"] = str(uuid.uuid4())
    agent["status"] = "draft"
    return await agent_repository.insert_one(agent)


@router.patch("/{agent_id}")
async def update_agent(agent_id: str, body: AgentUpdate):
    existing = await agent_repository.get_by_agent_id(agent_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    updates = {k: v for k, v in body.model_dump(exclude_unset=True).items()}
    if updates:
        await agent_repository.update_one({"agent_id": agent_id}, updates)
    return await agent_repository.get_by_agent_id(agent_id)


@router.post("/{agent_id}/activate")
async def activate_agent(agent_id: str):
    agent = await agent_repository.get_by_agent_id(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    db = get_database()
    now = datetime.utcnow()
    await db[Collections.AGENTS].update_many(
        {"status": "active"}, {"$set": {"status": "draft", "updated_at": now}}
    )
    await agent_repository.update_one({"agent_id": agent_id}, {"status": "active"})
    return await agent_repository.get_by_agent_id(agent_id)


@router.delete("/{agent_id}")
async def delete_agent(agent_id: str):
    agent = await agent_repository.get_by_agent_id(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    if agent.get("status") == "active":
        raise HTTPException(status_code=409, detail="Cannot delete the active agent — activate another first")
    await agent_repository.soft_delete({"agent_id": agent_id})
    return {"ok": True}
