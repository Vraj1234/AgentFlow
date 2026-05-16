"""Tests for the core agent framework — message bus and knowledge base."""

import pytest

from src.orchestrator.message_bus import MessageBus
from src.orchestrator.knowledge_base import KnowledgeBase
from src.agents.base import Agent, AgentRole, AgentMessage


class EchoAgent(Agent):
    """Test agent that echoes messages back to sender."""

    def __init__(self, message_bus, knowledge_base):
        super().__init__(AgentRole.SPEC_ANALYST, message_bus, knowledge_base)
        self.received: list[AgentMessage] = []

    async def process(self, message: AgentMessage) -> None:
        self.received.append(message)

    async def execute(self, task: dict) -> dict:
        return {"echo": task}


@pytest.mark.asyncio
async def test_message_bus_direct_delivery():
    bus = MessageBus()
    kb = KnowledgeBase()
    agent = EchoAgent(bus, kb)
    await agent.start()

    msg = AgentMessage(sender="test", recipient=agent.agent_id, content={"hello": "world"})
    await bus.publish(msg)

    assert len(agent.received) == 1
    assert agent.received[0].content == {"hello": "world"}
    await agent.stop()


@pytest.mark.asyncio
async def test_message_bus_broadcast():
    bus = MessageBus()
    kb = KnowledgeBase()
    a1 = EchoAgent(bus, kb)
    a2 = EchoAgent(bus, kb)
    await a1.start()
    await a2.start()

    msg = AgentMessage(sender="test", recipient="all", content={"broadcast": True})
    await bus.broadcast(msg, exclude="test")

    assert len(a1.received) == 1
    assert len(a2.received) == 1
    await a1.stop()
    await a2.stop()


@pytest.mark.asyncio
async def test_knowledge_base_put_and_get():
    kb = KnowledgeBase()
    await kb.put("spec", {"title": "Task Manager"}, author="spec_analyst", tags=("spec",))

    entry = await kb.get("spec")
    assert entry is not None
    assert entry.value == {"title": "Task Manager"}
    assert entry.author == "spec_analyst"


@pytest.mark.asyncio
async def test_knowledge_base_versioning():
    kb = KnowledgeBase()
    await kb.put("schema", {"v": 1}, author="architect")
    await kb.put("schema", {"v": 2}, author="architect")

    latest = await kb.get("schema")
    assert latest.value == {"v": 2}

    history = await kb.get_history("schema")
    assert len(history) == 2


@pytest.mark.asyncio
async def test_knowledge_base_search_by_tag():
    kb = KnowledgeBase()
    await kb.put("api_contract", {"endpoints": []}, author="architect", tags=("architecture",))
    await kb.put("db_schema", {"tables": []}, author="architect", tags=("architecture",))
    await kb.put("spec", {"features": []}, author="analyst", tags=("spec",))

    results = await kb.search("architecture")
    assert len(results) == 2
